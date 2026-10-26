import hashlib
import io
import json
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload import install_ops
from sideload import remote_binaries
from sideload.install_ops import download_asset, inspect_archive
from sideload.remote_binaries import prepare_remote_binaries

URL = "https://github.com/acme/example/releases/download/v1/engine.bin"
BINARY = b"verified dependency binary"


def dependency(name="engine.bin", body=BINARY):
    return {"name": name, "url": URL, "sha256hash": hashlib.sha256(body).hexdigest()}


class Transport:
    def __init__(self, body=BINARY, *, declared=None, cancel=None, fallback=False):
        self.body = body
        self.declared = declared
        self.cancel = cancel
        self.fallback = fallback
        self.calls = []

    def routes(self, url):
        return ["direct", "gh-proxy"] if self.fallback else ["gh-proxy"]

    @contextmanager
    def open(self, url, route, **kwargs):
        self.calls.append((url, route, kwargs))
        if self.fallback and route == "direct":
            raise ConnectionError("direct unavailable")
        with io.BytesIO(self.body) as response:
            response.headers = {"Content-Length": str(len(self.body) if self.declared is None else self.declared)}
            if self.cancel:
                self.cancel.set()
            yield response


class RemoteBinariesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.archive = self.home / "plugin.zip"
        self.metadata = {"name": "example", "version": "1.0.0", "custom": {"text": "保留", "list": [1, True]},
                         "remote_binary": [dependency()]}
        self.make_archive()

    def make_archive(self, extra=None):
        with ZipFile(self.archive, "w") as archive:
            archive.comment = b"preserve archive comment"
            archive.writestr("example/plugin.json", json.dumps({"name": "Example"}))
            archive.writestr("example/package.json", json.dumps(self.metadata, ensure_ascii=False))
            archive.writestr("example/dist/index.js", "export default {}")
            archive.writestr("example/LICENSE", "BSD")
            for name, body in (extra or {}).items():
                archive.writestr(name, body)

    def inspect(self):
        return inspect_archive(self.archive, None, [], "decky-sideloader", "decky-sideloader")

    def prepare(self, **kwargs):
        kwargs.setdefault("transport", Transport())
        return prepare_remote_binaries(self.archive, {"folder": "example"}, None, **kwargs)

    def assert_clean(self):
        self.assertFalse(list(self.home.glob(".remote-binaries-*")))

    def test_success_uses_proxy_preserves_source_and_reinspection_hashes(self):
        self.make_archive({"example/bin/engine.bin": b"old regular file", "example/bin/keep": b"untouched"})
        original = self.archive.read_bytes()
        before = self.inspect()
        progress = []
        transport = Transport(fallback=True)
        result = prepare_remote_binaries(self.archive, before, None, transport=transport, progress=progress.append, timeout_seconds=600)
        self.assertEqual(result, {"count": 1, "names": ["engine.bin"]})
        self.assertEqual((self.home / "source.zip").read_bytes(), original)
        self.assertEqual([call[1] for call in transport.calls], ["direct", "gh-proxy"])
        self.assertTrue(all(item["name"] == "engine.bin" and item["index"] == item["count"] == 1 for item in progress))
        self.assertEqual(progress[-1]["status"], "complete")
        after = self.inspect()
        self.assertEqual(after["hashes"]["bin/engine.bin"], hashlib.sha256(BINARY).hexdigest())
        self.assertNotEqual(after["hashes"]["package.json"], before["hashes"]["package.json"])
        self.assertNotEqual(after["sha256"], before["sha256"])
        with ZipFile(self.archive) as archive:
            self.assertEqual(archive.read("example/bin/engine.bin"), BINARY)
            self.assertEqual(archive.read("example/bin/keep"), b"untouched")
            self.assertEqual(archive.comment, b"preserve archive comment")
            self.assertEqual(json.loads(archive.read("example/package.json")), {key: value for key, value in self.metadata.items() if key != "remote_binary"})
            self.assertEqual(archive.namelist().count("example/bin/engine.bin"), 1)
        self.assertIsNone(self.prepare())
        self.assertEqual((self.home / "source.zip").read_bytes(), original)
        self.assert_clean()

    def test_multiple_dependencies_progress_is_one_based_and_hashes_are_case_insensitive(self):
        self.metadata["remote_binary"] = [dependency("first"), dependency("second")]
        self.metadata["remote_binary"][1]["sha256hash"] = self.metadata["remote_binary"][1]["sha256hash"].upper()
        self.make_archive()
        snapshots = []
        result = self.prepare(progress=snapshots.append)
        self.assertEqual(result, {"count": 2, "names": ["first", "second"]})
        self.assertEqual({(item["name"], item["index"], item["count"]) for item in snapshots}, {("first", 1, 2), ("second", 2, 2)})
        package = self.inspect()
        for name in ("first", "second"):
            self.assertEqual(package["hashes"][f"bin/{name}"], hashlib.sha256(BINARY).hexdigest())

    def test_missing_and_empty_manifest_are_noops(self):
        for missing in (True, False):
            if missing:
                self.metadata.pop("remote_binary", None)
            else:
                self.metadata["remote_binary"] = []
            self.make_archive()
            original = self.archive.read_bytes()
            self.assertEqual(self.prepare(), None if missing else {"count": 0, "names": []})
            self.assertEqual(self.archive.read_bytes(), original)
            self.assertFalse((self.home / "source.zip").exists())

    def test_bad_hash_leaves_original_and_cleans_temporary_files(self):
        self.metadata["remote_binary"] = [dependency("first"), dependency("second", b"wrong")]
        self.make_archive()
        original = self.archive.read_bytes()
        snapshots = []
        with self.assertRaisesRegex(RuntimeError, "second.*SHA-256"):
            self.prepare(progress=snapshots.append)
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertFalse((self.home / "source.zip").exists())
        self.assert_clean()
        self.assertEqual(snapshots[-1]["status"], "failed")
        self.assertEqual(snapshots[-1]["name"], "second")

    def test_rejects_unsafe_names_duplicate_names_hashes_and_unsupported_origins(self):
        manifests = []
        for name in ("../escape", "/absolute", "a/b", "a\\b", ".", "..", "nul\x00byte", "line\nbreak", "a" * 256):
            manifests.append(([dependency(name)], "安全"))
        manifests.extend([
            ([dependency(), dependency()], "重复"),
            ([{**dependency(), "sha256hash": "a" * 63}], "64"),
            ([{**dependency(), "sha256hash": "z" * 64}], "64"),
            ([{**dependency(), "url": "https://example.com/file"}], "不支持的依赖来源"),
            ([{**dependency(), "url": "http://github.com/acme/example/releases/download/v1/file"}], "不支持的依赖来源"),
            ([{**dependency(), "url": "https://github.com/acme/example/blob/main/file"}], "不支持的依赖来源"),
            (["bad"], "对象"),
            ({"name": "not a list"}, "数组"),
            (None, "数组"),
        ])
        for manifest, reason in manifests:
            with self.subTest(manifest=manifest):
                self.metadata["remote_binary"] = manifest
                self.make_archive()
                original = self.archive.read_bytes()
                transport = Transport()
                with self.assertRaisesRegex(RuntimeError, reason):
                    self.prepare(transport=transport)
                self.assertEqual(transport.calls, [])
                self.assertEqual(self.archive.read_bytes(), original)
                self.assert_clean()

    def test_rejects_file_directory_and_special_file_collisions(self):
        for path in ("example/bin", "example/bin/engine.bin/", "example/bin/engine.bin/child"):
            with self.subTest(path=path):
                self.make_archive({path: b"collision"})
                original = self.archive.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "engine.bin.*路径冲突"):
                    self.prepare()
                self.assertEqual(self.archive.read_bytes(), original)
        for path in ("example/bin", "example/bin/engine.bin"):
            self.make_archive()
            link = ZipInfo(path)
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with ZipFile(self.archive, "a") as archive:
                archive.writestr(link, "/outside")
            with self.assertRaisesRegex(RuntimeError, "路径冲突"):
                self.prepare()
        self.assert_clean()

    def test_original_and_download_sizes_both_count_even_when_replacing(self):
        self.make_archive({"example/bin/engine.bin": b"x" * 200})
        original = self.archive.read_bytes()
        with ZipFile(self.archive) as archive:
            expanded = sum(entry.file_size for entry in archive.infolist())
        with patch.object(remote_binaries, "MAX_EXPANDED", expanded + len(BINARY) - 1):
            with self.assertRaisesRegex(RuntimeError, "engine.bin.*大小上限"):
                self.prepare()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assert_clean()

    def test_aggregate_dependency_limit_and_entry_limit(self):
        self.metadata["remote_binary"] = [dependency("first"), dependency("second")]
        self.make_archive()
        original = self.archive.read_bytes()
        with ZipFile(self.archive) as archive:
            expanded = sum(entry.file_size for entry in archive.infolist())
            entries = len(archive.infolist())
        with patch.object(remote_binaries, "MAX_EXPANDED", expanded + len(BINARY) * 2 - 1):
            with self.assertRaisesRegex(RuntimeError, "second.*大小上限"):
                self.prepare()
        with patch.object(remote_binaries, "MAX_ENTRIES", entries + 1):
            with self.assertRaisesRegex(RuntimeError, "文件数"):
                self.prepare()
        with patch.object(remote_binaries, "MAX_EXPANDED", expanded - 1):
            with self.assertRaisesRegex(RuntimeError, "解压大小"):
                self.prepare()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assert_clean()

    def test_dependency_download_budget_exceeds_180_mib_without_changing_main_cap(self):
        observed = []
        real_download = download_asset
        def record(*args, **kwargs):
            observed.append(kwargs["max_bytes"])
            return real_download(*args, **kwargs)
        with patch.object(remote_binaries, "download_asset", side_effect=record):
            self.prepare()
        self.assertGreater(observed[0], 180 * 1024 * 1024)
        self.assertEqual(install_ops.MAX_DOWNLOAD, 128 * 1024 * 1024)
        large = Transport(declared=180882678)
        with self.assertRaisesRegex(RuntimeError, "大小上限"):
            download_asset(URL, self.home / "main.zip", None, transport=large)
        with self.assertRaisesRegex(RuntimeError, "下载不完整"):
            download_asset(URL, self.home / "dependency.bin", None, transport=large, max_bytes=512 * 1024 * 1024)

    def test_download_without_content_length_still_obeys_custom_bound(self):
        transport = Transport()
        @contextmanager
        def response(*args, **kwargs):
            with io.BytesIO(BINARY) as body:
                body.headers = {}
                yield body
        with patch.object(transport, "open", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "过大"):
                download_asset(URL, self.home / "bound.bin", None, transport=transport, max_bytes=len(BINARY) - 1)

    def test_cancel_before_and_during_download_leaves_original(self):
        for before in (True, False):
            cancel = threading.Event()
            if before:
                cancel.set()
            original = self.archive.read_bytes()
            snapshots = []
            with self.assertRaisesRegex(RuntimeError, "停止"):
                self.prepare(cancel=cancel, transport=Transport(cancel=cancel), progress=snapshots.append)
            self.assertEqual(self.archive.read_bytes(), original)
            self.assertFalse((self.home / "source.zip").exists())
            self.assert_clean()
            if not before:
                self.assertEqual(snapshots[-1]["status"], "failed")

    def test_cancel_while_copying_original_entries_and_preserving_source(self):
        for during_source in (False, True):
            with self.subTest(during_source=during_source):
                cancel = threading.Event()
                original = self.archive.read_bytes()
                snapshots = []
                opener = Path.open if during_source else ZipFile.open
                class CancelReader:
                    def __init__(self, stream):
                        self.stream = stream
                    def __enter__(self):
                        self.stream.__enter__()
                        return self
                    def __exit__(self, *args):
                        return self.stream.__exit__(*args)
                    def read(self, *args):
                        data = self.stream.read(*args)
                        cancel.set()
                        return data
                def open_file(path, *args, **kwargs):
                    stream = opener(path, *args, **kwargs)
                    return CancelReader(stream) if path == self.archive and args == ("rb",) else stream
                def open_entry(archive, entry, mode="r", *args, **kwargs):
                    stream = opener(archive, entry, mode, *args, **kwargs)
                    name = entry.filename if isinstance(entry, ZipInfo) else entry
                    return CancelReader(stream) if mode == "r" and name == "example/dist/index.js" else stream
                with patch.object(Path if during_source else ZipFile, "open", open_file if during_source else open_entry):
                    with self.assertRaisesRegex(RuntimeError, "停止"):
                        self.prepare(cancel=cancel, progress=snapshots.append)
                self.assertEqual(self.archive.read_bytes(), original)
                self.assertFalse((self.home / "source.zip").exists())
                self.assert_clean()
                if during_source:
                    self.assertEqual(snapshots[-1]["status"], "failed")

    def test_existing_source_is_refreshed_only_after_success_and_symlinks_rejected(self):
        source = self.home / "source.zip"
        source.write_bytes(b"previous attempt")
        original = self.archive.read_bytes()
        self.prepare()
        self.assertEqual(source.read_bytes(), original)
        self.make_archive()
        source.unlink()
        outside = self.home / "outside"
        outside.write_bytes(b"keep")
        source.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "路径不安全"):
            self.prepare()
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
