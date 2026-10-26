import json
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload import install_ops
from sideload.github_transport import _Budget, GitHubTransport
from test_transport import ZIP, trickling_response
from sideload.install_ops import download_asset, backup_data, inspect_archive, plugin_stamp, verify_installed


def make_package(path, *, name="Example", folder="example", version="2.0.0", extra=None):
    files = {
        f"{folder}/plugin.json": json.dumps({"name": name, "flags": []}),
        f"{folder}/package.json": json.dumps({"version": version}),
        f"{folder}/dist/index.js": "export default () => ({});",
        f"{folder}/LICENSE": "BSD",
    }
    files.update(extra or {})
    with ZipFile(path, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)
    return path


class InstallOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.zip = make_package(self.home / "plugin.zip")

    def inspect(self, existing=None, installed=None):
        return inspect_archive(self.zip, existing, installed or [], "decky-sideloader", "decky-sideloader")


    def test_spaced_directory_name_is_one_plugin_folder(self):
        path = self.home / "spaced.zip"
        make_package(path, name="DLSS Enabler", folder="DLSS Enabler")
        package = inspect_archive(path, {"name": "DLSS Enabler", "folder": "DLSS Enabler"}, [], "decky-sideloader", "decky-sideloader")
        self.assertEqual(package["folder"], "DLSS Enabler")
    def test_valid_package_has_real_identity_and_hashes(self):
        package = self.inspect()
        self.assertEqual(package["name"], "Example")
        self.assertEqual(package["version"], "2.0.0")
        self.assertEqual(len(package["sha256"]), 64)
        self.assertIn("dist/index.js", package["hashes"])

    def test_rejects_path_escape_absolute_and_windows_paths(self):
        for key in ("example/../../outside", "/outside", "example\\bad", "other/file", "example//bad"):
            with self.subTest(key=key):
                make_package(self.zip, extra={key: "bad"})
                with self.assertRaises(RuntimeError):
                    self.inspect()

    def test_rejects_symlinks(self):
        link = ZipInfo("example/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with ZipFile(self.zip, "a") as archive:
            archive.writestr(link, "/etc/passwd")
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_rejects_duplicate_and_file_directory_collision(self):
        make_package(self.zip, extra={"example/dist": "file"})
        with self.assertRaises(RuntimeError):
            self.inspect()

    def test_rejects_missing_frontend_and_license(self):
        for missing in ("example/dist/index.js", "example/LICENSE"):
            make_package(self.zip)
            with ZipFile(self.zip) as archive:
                files = {k: archive.read(k) for k in archive.namelist() if k != missing}
            with ZipFile(self.zip, "w") as archive:
                for k, v in files.items():
                    archive.writestr(k, v)
            if missing.endswith("LICENSE"):
                self.assertIn("LICENSE", self.inspect()["warnings"][0])
            else:
                with self.assertRaises(RuntimeError):
                    self.inspect()

    def test_rejects_self_and_changed_folder_or_identity(self):
        with self.assertRaises(RuntimeError):
            self.inspect({"name": "Different", "folder": "example"})
        with self.assertRaises(RuntimeError):
            self.inspect({"name": "Example", "folder": "old-folder"})
        for name, folder in (("decky-sideloader", "example"), ("Example", "decky-sideloader")):
            make_package(self.zip, name=name, folder=folder)
            with self.assertRaises(RuntimeError):
                self.inspect()

    def test_new_install_cannot_overwrite_existing_plugin(self):
        with self.assertRaises(RuntimeError):
            self.inspect(installed=[{"name": "Example", "folder": "example"}])

    def test_archive_limits(self):
        with patch.object(install_ops, "MAX_EXPANDED", 3):
            with self.assertRaises(RuntimeError):
                self.inspect()
        with patch.object(install_ops, "MAX_ENTRIES", 1):
            with self.assertRaises(RuntimeError):
                self.inspect()

    def test_backup_copies_standard_data_and_manifest(self):
        source = self.home / "settings" / "example"
        source.mkdir(parents=True)
        (source / "user.json").write_text('{"setting":true}')
        data = self.home / "data" / "example"
        data.mkdir(parents=True)
        (data / "cache.bin").write_bytes(b"data")
        backup = backup_data(self.home, "example", self.home / "backup.zip")
        self.assertEqual(backup["files"], 2)
        with ZipFile(backup["archive"]) as archive:
            self.assertEqual(archive.read("settings/example/user.json"), b'{"setting":true}')
            self.assertIsNone(archive.testzip())
        self.assertEqual(stat.S_IMODE(Path(backup["archive"]).stat().st_mode), 0o600)
        self.assertTrue((source / "user.json").exists())

    def test_backup_absent_dirs_is_explicit_empty_snapshot(self):
        result = backup_data(self.home, "example", self.home / "backup.zip")
        self.assertEqual(result["paths"], [])
        self.assertEqual(result["files"], 0)

    def test_backup_rejects_symlink_and_size_overflow(self):
        source = self.home / "settings" / "example"
        source.mkdir(parents=True)
        (source / "data").write_text("hello")
        with patch.object(install_ops, "MAX_BACKUP", 1):
            with self.assertRaises(RuntimeError):
                backup_data(self.home, "example", self.home / "small.zip")
        (source / "link").symlink_to(source / "data")
        with self.assertRaises(RuntimeError):
            backup_data(self.home, "example", self.home / "link.zip")

    def test_backup_permission_error_is_not_an_empty_success(self):
        source = self.home / "settings" / "example"
        source.mkdir(parents=True)
        def denied(*args, **kwargs):
            kwargs["onerror"](PermissionError("denied"))
            yield
        with patch.object(install_ops.os, "walk", side_effect=denied):
            with self.assertRaises(PermissionError):
                backup_data(self.home, "example", self.home / "backup.zip")

    def test_verification_requires_a_change_and_matching_content(self):
        package = self.inspect()
        with ZipFile(self.zip) as archive:
            archive.extractall(self.home / "plugins")  # Only the locally constructed safe fixture.
        stamp = plugin_stamp(self.home, "example")
        self.assertFalse(verify_installed(self.home, package, stamp))
        self.assertTrue(verify_installed(self.home, package, None))
        (self.home / "plugins" / "example" / "dist" / "index.js").write_text("corrupted")
        self.assertFalse(verify_installed(self.home, package, None))

    def test_download_host_allowlist(self):
        self.assertTrue(install_ops._allowed_download("https://github.com/acme/foo/releases/download/v1/foo.zip", initial=True))
        for url in ("http://github.com/acme/foo/releases/download/v1/foo.zip", "https://localhost/a.zip", "file:///etc/passwd", "https://github.com.evil.test/a.zip"):
            self.assertFalse(install_ops._allowed_download(url, initial=True))

    def test_download_interrupts_framing_and_bounds_each_fallback_deadline(self):
        transport = GitHubTransport(None, "auto")
        opened = transport.open
        attempts, events = [], []
        target = self.home / "download.zip"
        def short_budget(_expires, cancel, **kwargs):
            return _Budget(time.monotonic() + 0.07, cancel, **kwargs)
        @contextmanager
        def open_route(url, route, **kwargs):
            attempts.append(route)
            if route == "direct":
                time.sleep(0.025)
                raise TimeoutError("offline")
            with trickling_response(prefix):
                with opened(url, route, **kwargs) as response:
                    yield response
        prefix = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;extension="
        with patch.object(install_ops, "_Budget", side_effect=short_budget), patch.object(transport, "open", side_effect=open_route):
            start = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "3 分钟"):
                download_asset(ZIP, target, None, transport=transport, progress=events.append)
            self.assertLess(time.monotonic() - start, 0.3)
        self.assertEqual(attempts, ["direct", "gh-proxy", "ghfast"])
        self.assertEqual(events[-1]["status"], "failed")
        self.assertEqual(events[-1]["attempt"], 3)
        self.assertEqual(target.read_bytes(), b"")

    def test_download_response_timeout_falls_back_but_does_not_cap_body_duration(self):
        transport = GitHubTransport(None, "auto")
        opened, attempts, events = transport.open, [], []
        target = self.home / "download.zip"
        @contextmanager
        def route_response(url, route, **kwargs):
            attempts.append(route)
            prefix = b"HTTP/1.1 200 OK\r\nX-Slow: " if route == "direct" else b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n"
            body = b"x" * 200 if route == "direct" else b"0123456789"
            with trickling_response(prefix, tail=body, interval=0.01):
                with opened(url, route, **kwargs) as response:
                    yield response
        with patch.object(install_ops, "DOWNLOAD_RESPONSE_SECONDS", 0.06), patch.object(transport, "open", side_effect=route_response):
            download_asset(ZIP, target, None, transport=transport, progress=events.append)
        self.assertEqual(attempts, ["direct", "gh-proxy"])
        self.assertEqual(target.read_bytes(), b"0123456789")
        self.assertEqual(events[-1]["status"], "complete")
