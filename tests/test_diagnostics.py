import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "py_modules"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
import diagnostics as module
from diagnostics import Diagnostics, LOG_BYTES, LINE_BYTES, PACKAGE_BYTES, LOG_MARKER, ZIP_MARKER, LOG_NAMES, encoded, sanitize
from install_flow import InstallFlow


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.logs = self.root / "logs"
        self.home = self.root / "user"
        self.service = Diagnostics(str(self.logs), str(self.home), {"sdk": "1.0.0", "backend": "0.0.1", "frontend": "0.0.1", "loader": "v3.2.10"})
        self.export_dir = self.home / "Downloads" / "GitHub-Sideload-Diagnostics"
        self.canaries = ["Bearer SYNTHETIC_BEARER_SECRET", "eyJhbGciOiJIUzI1NiJ9.eyJjYW5hcnkiOiJzZWNyZXQifQ.SYNTHETIC_JWT", "token=SYNTHETIC_TOKEN", "password=SYNTHETIC_PASSWORD", "cookie=SYNTHETIC_COOKIE", "privateKey=SYNTHETIC_KEY", "-----BEGIN PRIVATE KEY-----SYNTHETIC_PEM-----END PRIVATE KEY-----", "https://SYNTHETIC_USER:SYNTHETIC_PASS@example.org/repo?secret=SYNTHETIC_QUERY", "/home/SYNTHETIC_HOME/private"]

    async def asyncTearDown(self):
        await self.service.close()

    def log_files(self):
        return [self.logs / "diagnostics" / name for name in LOG_NAMES if (self.logs / "diagnostics" / name).exists()]

    def assert_private(self, path, mode):
        self.assertEqual(path.stat().st_mode & 0o777, mode)

    async def test_utf8_huge_fields_rotation_and_total_bytes(self):
        huge = "中文" * (1024 * 1024)
        # Unknown text is dropped before JSON encoding, never truncated mid-codepoint.
        row = sanitize("rpc_failed", {"rpc": "list_releases", "message": huge, "stage": huge, "operationId": huge})
        self.assertEqual(row, {"event": "rpc_failed", "rpc": "list_releases"})
        for n in range(1500):
            self.service.record("rpc_failed", rpc="list_releases", message=huge, count=n)
        # Exercise all four segments cheaply while keeping the exact production thresholds.
        directory = self.logs / "diagnostics"
        for _ in range(5):
            for canary in self.canaries:
                self.service.record("rpc_failed", message=canary)
            with (directory / "active.jsonl").open("ab") as stream:
                remaining = LOG_BYTES - stream.tell()
                stream.write(b" " * remaining)
            self.service.record("rpc_end", rpc="list_releases")
        files = self.log_files()
        self.assertEqual(len(files), 4)
        self.assertLessEqual(sum(p.stat().st_size for p in files), 4 * LOG_BYTES)
        # Independently ensure an enormous Unicode event never makes any new oversized line.
        self.service.record("rpc_failed", message=huge, password=huge)
        active = directory / "active.jsonl"
        for line in active.read_bytes().splitlines(keepends=True):
            self.assertLessEqual(len(line), LINE_BYTES)
            json.loads(line)
        self.assert_private(directory, 0o700)
        for file in files:
            self.assert_private(file, 0o600)
            for canary in self.canaries:
                self.assertNotIn(canary.encode(), file.read_bytes())

    async def test_canaries_are_absent_in_logs_and_export_even_from_historical_lines(self):
        for canary in self.canaries:
            self.service.record("rpc_failed", message=canary, url=canary, password=canary, operationId=canary, module=canary)
        active = self.logs / "diagnostics" / "active.jsonl"
        with active.open("ab") as stream:
            for canary in self.canaries:
                stream.write(encoded({"event": "rpc_failed", "rpc": "list_releases", "message": canary, "traceback": canary, "timestamp": 1}))
            stream.write(b"not json SYNTHETIC_BAD\n")
            stream.write(encoded({"event": "unknown", "secret": "SYNTHETIC_BAD_EVENT"}))
        result = await self.service.export({"stage": "closed", "operationId": "b" * 32, "mode": "direct", "config": self.canaries})
        self.assertEqual(result["state"], "success")
        with zipfile.ZipFile(result["path"]) as archive:
            payloads = [archive.read(name) for name in archive.namelist()]
            combined = b"".join(payloads)
            for canary in self.canaries + ["SYNTHETIC_BAD", "SYNTHETIC_BAD_EVENT"]:
                self.assertNotIn(canary.encode(), combined)
            summary = json.loads(archive.read("summary.json"))
            self.assertEqual(summary["invalidLines"], 2)
            self.assertEqual(summary["snapshot"]["stage"], "closed")
            self.assertEqual(summary["versions"]["loader"], "v3.2.10")
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest["files"]:
                value = archive.read(item["name"])
                self.assertEqual(item["bytes"], len(value))
                self.assertEqual(item["sha256"], hashlib.sha256(value).hexdigest())
            self.assertLessEqual(sum(map(len, payloads)), PACKAGE_BYTES)
            for name in archive.namelist():
                if name.endswith(".jsonl"):
                    for line in archive.read(name).splitlines(keepends=True):
                        self.assertLessEqual(len(line), LINE_BYTES)
        self.assertLessEqual(result["bytes"], PACKAGE_BYTES)
        self.assertEqual(result["bytes"], Path(result["path"]).stat().st_size)
        self.assert_private(Path(result["path"]), 0o600)
        self.assert_private(self.export_dir, 0o700)
        # Newly produced disk records also have no canaries; manually planted old payloads are only in this fixture.
        for line in active.read_bytes().splitlines()[:len(self.canaries) + 1]:
            for canary in self.canaries:
                self.assertNotIn(canary.encode(), line)

    async def test_batch_validation_rejects_unbounded_or_unknown_data(self):
        event = {"event": "loader_read", "ok": True, "count": 25, "durationMs": 10}
        self.assertEqual(self.service.record_batch([event] * 16, 65535), {"accepted": 16})
        for events, dropped in (([event] * 17, 0), ([{"event": "loader_read"}], 0), ([{**event, "headers": self.canaries}], 0), ([{**event, "count": True}], 0), ([{**event, "durationMs": "中文" * 10000}], 0), ([event], 65536), ([event], True), (None, 0)):
            with self.assertRaises(RuntimeError):
                self.service.record_batch(events, dropped)
        self.assertLessEqual(max(map(len, (self.logs / "diagnostics" / "active.jsonl").read_bytes().splitlines(keepends=True))), LINE_BYTES)

    async def test_retention_preserves_unknown_matching_files_and_restart_cleans_only_own_temps(self):
        self.service.record("startup")
        self.export_dir.mkdir(parents=True)
        unknown = self.export_dir / "personal.zip"
        unknown.write_bytes(b"keep me")
        matching_unknown = self.export_dir / ("diagnostics-" + "0" * 19 + "-" + "a" * 32 + ".zip")
        matching_unknown.write_bytes(b"foreign")
        stale = self.export_dir / ("building-" + "b" * 32 + ".tmp")
        stale.write_bytes(ZIP_MARKER + b"crash interrupted")
        other_temp = self.export_dir / ("building-" + "c" * 32 + ".tmp")
        other_temp.write_bytes(b"foreign temp")
        results = []
        for _ in range(6):
            results.append(await self.service.export({}))
        self.assertTrue(all(r["state"] == "success" for r in results))
        self.assertFalse(stale.exists())
        self.assertEqual(unknown.read_bytes(), b"keep me")
        self.assertEqual(matching_unknown.read_bytes(), b"foreign")
        self.assertEqual(other_temp.read_bytes(), b"foreign temp")
        self.assertFalse(Path(results[0]["path"]).exists())
        own = [Path(r["path"]) for r in results if Path(r["path"]).exists()]
        self.assertEqual(len(own), 3)
        self.assertLessEqual(sum(p.stat().st_size for p in own), 18 * 1024 * 1024)
        reloaded = Diagnostics(str(self.logs), str(self.home))
        result = await reloaded.export({})
        self.assertEqual(result["state"], "success")
        await reloaded.close()

    async def test_links_foreign_logs_oversized_files_and_missing_sdk_home_fail_closed(self):
        self.logs.mkdir()
        target = self.root / "foreign"
        target.write_bytes(b"secret external content")
        (self.logs / "diagnostics").symlink_to(self.root, target_is_directory=True)
        self.service.record("startup")
        self.assertEqual(self.service.health["dropped"], 1)
        self.assertEqual(target.read_bytes(), b"secret external content")
        (self.logs / "diagnostics").unlink()
        (self.logs / "diagnostics").mkdir()
        active = self.logs / "diagnostics" / "active.jsonl"
        active.symlink_to(target)
        self.service.record("startup")
        self.assertEqual((await self.service.export({}))["state"], "error")
        active.unlink()
        os.link(target, active)
        self.service.record("startup")
        self.assertEqual(target.read_bytes(), b"secret external content")
        self.assertEqual((await self.service.export({}))["state"], "error")
        active.unlink()
        active.write_bytes(b"foreign")
        self.service.record("startup")
        self.assertEqual(active.read_bytes(), b"foreign")
        self.assertEqual((await self.service.export({}))["state"], "error")
        active.unlink()
        with active.open("wb") as stream:
            stream.write(LOG_MARKER)
            stream.truncate(LOG_BYTES * 10)
        self.service.record("startup")
        self.assertLessEqual(active.stat().st_size, LOG_BYTES)
        # Matching export links are ignored, not followed or deleted.
        self.export_dir.mkdir(parents=True, exist_ok=True)
        linked = self.export_dir / ("diagnostics-" + "1" * 19 + "-" + "d" * 32 + ".zip")
        linked.symlink_to(target)
        hard = self.export_dir / ("diagnostics-" + "2" * 19 + "-" + "e" * 32 + ".zip")
        os.link(target, hard)
        self.assertEqual((await self.service.export({}))["state"], "success")
        self.assertTrue(linked.is_symlink())
        self.assertTrue(hard.exists())
        for home in ("", "/root", "relative"):
            invalid = Diagnostics(str(self.logs), home)
            self.assertEqual((await invalid.export({}))["state"], "error")
            await invalid.close()

    async def test_single_export_worker_busy_cancel_failure_retry_and_cleanup(self):
        self.service.record("startup")
        ready, release = threading.Event(), threading.Event()
        original = self.service._snapshot
        def snapshot():
            ready.set()
            release.wait(5)
            return original()
        with patch.object(self.service, "_snapshot", side_effect=snapshot):
            first = asyncio.create_task(self.service.export({}))
            await asyncio.to_thread(ready.wait, 5)
            self.assertEqual(self.service.status(), {"state": "exporting"})
            with self.assertRaisesRegex(RuntimeError, "正在进行"):
                await self.service.export({})
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            # RPC cancellation does not orphan the worker; close waits and worker cleans.
            release.set()
            await self.service.close()
        self.assertFalse(list(self.export_dir.glob("building-*.tmp")))
        self.service = Diagnostics(str(self.logs), str(self.home))
        with patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("SYNTHETIC_DISK_FULL")):
            self.assertEqual((await self.service.export({}))["state"], "error")
        self.assertFalse(list(self.export_dir.glob("building-*.tmp")))
        self.assertEqual((await self.service.export({}))["state"], "success")

    async def test_sdk_exact_file_handler_removed_no_message_or_traceback_and_foreign_handler_untouched(self):
        self.logs.mkdir()
        sdk_path = self.logs / "sdk-timestamp.log"
        other_path = self.logs / "not-sdk.log"
        logger = logging.Logger("isolated-test")
        sdk = logging.FileHandler(sdk_path)
        other = logging.FileHandler(other_path)
        logger.addHandler(sdk)
        logger.addHandler(other)
        self.addCleanup(other.close)
        logger.setLevel(logging.INFO)
        self.service.attach_sdk(logger, str(sdk_path))
        self.assertNotIn(sdk, logger.handlers)
        self.assertIn(other, logger.handlers)
        before = sdk_path.stat().st_size
        for canary in self.canaries:
            logger.error("secret %s", canary, exc_info=(ValueError, ValueError(canary), None))
        self.assertEqual(sdk_path.stat().st_size, before)
        for file in self.log_files():
            for canary in self.canaries:
                self.assertNotIn(canary.encode(), file.read_bytes())
        await self.service.close()
        self.assertNotIn(self.service.sdk_handler, logger.handlers)

    async def test_logging_failure_never_changes_rpc_or_persist_order_and_closed_mako_not_replayed(self):
        plugin = main.Plugin()
        plugin._diagnostics = Mock(record=Mock(side_effect=OSError("full")))
        self.assertEqual(await plugin.ping(), "pong")
        with self.assertRaisesRegex(RuntimeError, "连接模式无效"):
            await plugin.set_network_mode("bad")
        runtime = self.root / "runtime"
        flow = InstallFlow(str(self.root), str(runtime), "self", "self", Mock(), Mock(), observer=Mock(side_effect=OSError("full")))
        flow.operation = {"id": "b57e4549130141ba8663a5266e940946", "stage": "unknown", "saw_finish": False}
        observed = []
        def observe(event, **fields):
            persisted = json.loads(flow.file.read_text())
            self.assertEqual(persisted, flow.operation)
            observed.append(fields)
            raise OSError("full")
        flow.observer = observe
        flow._update(stage="closed")
        self.assertEqual(len(observed), 1)
        saved = flow.operation
        with patch("install_flow.save_settings", side_effect=OSError("persist failed")):
            with self.assertRaisesRegex(OSError, "persist failed"):
                flow._update(stage="unknown")
        self.assertIs(flow.operation, saved)
        self.assertEqual(len(observed), 1)
        reloaded = InstallFlow(str(self.root), str(runtime), "self", "self", Mock(), Mock(), observer=observe)
        self.assertEqual(reloaded.operation["stage"], "closed")
        self.assertFalse(reloaded.operation["saw_finish"])
        self.assertNotIn("outcome", reloaded.operation)
        self.assertIsNone(reloaded.task)
        plugin._diagnostics = self.service
        plugin._flow = reloaded
        with patch.object(main.decky, "DECKY_PLUGIN_SETTINGS_DIR", str(self.root / "settings"), create=True), patch.object(plugin, "_install_flow", side_effect=AssertionError("must not recover")):
            self.assertEqual((await plugin.export_diagnostics())["state"], "success")
        self.assertEqual(reloaded.operation, saved)

    async def test_download_observation_only_on_status_route_attempt_changes(self):
        observed = Mock()
        flow = InstallFlow(str(self.root), str(self.root / "runtime"), "self", "self", Mock(), Mock(), observer=observed)
        flow.operation = {"id": "a" * 32, "stage": "checking", "download": None}
        for received in range(100):
            flow._update(download={"received": received, "status": "downloading", "route": "direct", "attempt": 1})
        self.assertEqual(observed.call_count, 1)
        flow._update(download={"received": 100, "status": "complete", "route": "direct", "attempt": 1})
        self.assertEqual(observed.call_count, 2)


    async def test_full_log_snapshot_and_transient_storage_are_bounded(self):
        self.service.record("startup")
        directory = self.logs / "diagnostics"
        row = encoded({"event": "rpc_end", "rpc": "list_releases", "durationMs": 1})
        data = LOG_MARKER + row * ((LOG_BYTES - len(LOG_MARKER)) // len(row))
        for name in LOG_NAMES:
            (directory / name).write_bytes(data)
        self.assertLessEqual(sum(p.stat().st_size for p in self.log_files()), 4 * LOG_BYTES)
        original = zipfile.ZipFile.writestr
        peaks = []
        def observed_write(archive, *args, **kwargs):
            result = original(archive, *args, **kwargs)
            temporary = list(self.export_dir.glob("building-*.tmp"))
            self.assertEqual(len(temporary), 1)
            self.assertLessEqual(temporary[0].stat().st_size, PACKAGE_BYTES)
            packages = list(self.export_dir.glob("diagnostics-*.zip"))
            self.assertLessEqual(len(packages), 3)
            total = sum(p.stat().st_size for p in self.log_files() + packages + temporary)
            peaks.append(total)
            self.assertLessEqual(total, 28 * 1024 * 1024)
            return result
        with patch.object(zipfile.ZipFile, "writestr", new=observed_write):
            for _ in range(4):
                result = await self.service.export({})
                self.assertEqual(result["state"], "success")
        self.assertTrue(peaks)
        self.assertFalse(list(self.export_dir.glob("building-*.tmp")))
        self.assertLessEqual(sum(p.stat().st_size for p in self.log_files() + list(self.export_dir.glob("diagnostics-*.zip"))), 22 * 1024 * 1024)
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertLessEqual(sum(item.file_size for item in archive.infolist()), PACKAGE_BYTES)
            self.assertEqual(len([name for name in archive.namelist() if name.startswith("logs/")]), 4)

    async def test_startup_recovery_no_index_or_external_directory_traversal(self):
        self.service.record("startup")
        self.export_dir.mkdir(parents=True)
        stale = self.export_dir / ("building-" + "f" * 32 + ".tmp")
        stale.write_bytes(ZIP_MARKER + b"partial")
        unknown = self.export_dir / "ownership.json"
        unknown.write_bytes(b"corrupt foreign index")
        oversized = self.export_dir / ("diagnostics-" + "0" * 19 + "-" + "1" * 32 + ".zip")
        with oversized.open("wb") as file:
            file.write(ZIP_MARKER)
            file.truncate(PACKAGE_BYTES * 10)
        restarted = Diagnostics(str(self.logs), str(self.home))
        self.assertFalse(stale.exists())
        self.assertFalse(oversized.exists())
        self.assertEqual(unknown.read_bytes(), b"corrupt foreign index")
        await restarted.close()
        other_home = self.root / "linked-user"
        other_home.mkdir()
        external = self.root / "external-downloads"
        external.mkdir()
        (other_home / "Downloads").symlink_to(external, target_is_directory=True)
        linked = Diagnostics(str(self.logs), str(other_home))
        self.assertEqual((await linked.export({}))["state"], "error")
        self.assertEqual(list(external.iterdir()), [])
        await linked.close()

    async def test_failures_before_marker_cleanup_and_unstringifiable_payloads(self):
        class NoString:
            def __str__(self):
                raise AssertionError("never stringify arbitrary payloads")
            def __repr__(self):
                raise AssertionError("never repr arbitrary payloads")
        self.service.record("rpc_failed", rpc="list_releases", message=NoString(), module=NoString(), operationId=NoString())
        self.assertEqual(self.service.health["dropped"], 0)
        self.assertNotIn(b"NoString", (self.logs / "diagnostics" / "active.jsonl").read_bytes())
        # Failure after exclusive create, before the ownership marker is written.
        original = module.os.fdopen
        def disk_full(file, mode, **kwargs):
            if mode in ("wb", "ab"):
                raise OSError("full before marker")
            return original(file, mode, **kwargs)
        with patch.object(module.os, "fdopen", side_effect=disk_full):
            self.assertEqual((await self.service.export({}))["state"], "error")
        self.assertFalse(list(self.export_dir.glob("building-*.tmp")))
        self.assertEqual((await self.service.export({}))["state"], "success")
        fresh = Diagnostics(str(self.root / "fresh-logs"), str(self.home))
        with patch.object(module.os, "fdopen", side_effect=disk_full):
            fresh.record("startup")
        self.assertFalse((self.root / "fresh-logs" / "diagnostics" / "active.jsonl").exists())
        fresh.record("startup")
        self.assertTrue((self.root / "fresh-logs" / "diagnostics" / "active.jsonl").exists())
        await fresh.close()

    async def test_main_lifecycle_initializes_only_private_temp_sdk_paths_and_stops_polling(self):
        sdk_file = self.root / "sdk.log"
        logger = logging.Logger("isolated-lifecycle")
        sdk = logging.FileHandler(sdk_file)
        logger.addHandler(sdk)
        logger.setLevel(logging.INFO)
        plugin = main.Plugin()
        with patch.multiple(main.decky, create=True, logger=logger, DECKY_PLUGIN_LOG_DIR=str(self.root / "lifecycle-logs"),
                DECKY_PLUGIN_LOG=str(sdk_file), DECKY_USER_HOME=str(self.home), DECKY_PLUGIN_VERSION="0.9.7", DECKY_VERSION="v3.2.10"):
            await plugin._main()
            self.assertEqual(plugin._diagnostics.versions["frontend"], "0.9.7")
            self.assertNotIn(sdk, logger.handlers)
            self.assertFalse(plugin._poll_task.done())
            await plugin._unload()
        self.assertTrue(plugin._poll_task.done())
        data = (self.root / "lifecycle-logs" / "diagnostics" / "active.jsonl").read_bytes()
        self.assertIn(b'"event":"startup"', data)
        self.assertIn(b'"event":"shutdown"', data)
        self.assertEqual(sdk_file.stat().st_size, 0)
        self.assertEqual(logger.handlers, [])

    async def test_observed_transport_logs_actual_failed_and_successful_attempts_without_urls(self):
        import io
        import urllib.error
        class Response(io.BytesIO):
            headers = {}
            status = 200
            def geturl(self):
                return "https://gh-proxy.org/https://api.github.com/repos/acme/plugin/releases"
        opener = Mock(open=Mock(side_effect=[urllib.error.URLError(TimeoutError("SYNTHETIC_TOKEN")), Response(b"[]")]))
        plugin = main.Plugin()
        plugin._diagnostics = self.service
        with patch.object(main.decky, "DECKY_PLUGIN_SETTINGS_DIR", str(self.root / "settings"), create=True):
            await plugin.set_network_mode("auto")
            with patch.object(main.urllib.request, "build_opener", return_value=opener):
                transport = plugin._transport()
                self.assertEqual(await asyncio.to_thread(transport.fetch_json, "https://api.github.com/repos/acme/plugin/releases"), [])
            await asyncio.sleep(0)
        rows = [json.loads(line) for line in (self.logs / "diagnostics" / "active.jsonl").read_bytes().splitlines()]
        attempts = [row for row in rows if row["event"] == "network_attempt"]
        self.assertEqual([(r["route"], r["attempt"], r["status"]) for r in attempts], [("direct", 1, "connecting"), ("direct", 1, "failed"), ("gh-proxy", 2, "connecting"), ("gh-proxy", 2, "ok")])
        self.assertEqual(plugin._last_route["route"], "gh-proxy")
        self.assertNotIn(b"SYNTHETIC_TOKEN", encoded(rows))
        self.assertNotIn(b"https://", encoded(rows))


    async def test_exclusive_publication_never_overwrites_a_foreign_destination(self):
        self.service.record("startup")
        self.export_dir.mkdir(parents=True)
        name = "diagnostics-1790000000000000000-" + "a" * 32 + ".zip"
        foreign = self.export_dir / name
        foreign.write_bytes(b"foreign destination")
        with patch.object(module.time, "time_ns", return_value=1790000000000000000), patch.object(module.uuid, "uuid4", return_value=types.SimpleNamespace(hex="a" * 32)):
            result = await self.service.export({})
        self.assertEqual(result["state"], "error")
        self.assertEqual(foreign.read_bytes(), b"foreign destination")
        self.assertFalse(list(self.export_dir.glob("building-*.tmp")))
        self.assertEqual((await self.service.export({}))["state"], "success")


    async def test_giant_unicode_and_nested_historical_bad_lines_are_counted_not_exported(self):
        self.service.record("startup")
        active = self.logs / "diagnostics" / "active.jsonl"
        with active.open("ab") as file:
            file.write(encoded({"event": "rpc_failed", "message": "SYNTHETIC_HUGE_CANARY" + "中文" * 10000}))
            file.write(b"[" * 1500 + b"0" + b"]" * 1500 + b"\n")
            file.write(b"SYNTHETIC_INCOMPLETE_CANARY")
        result = await self.service.export({})
        self.assertEqual(result["state"], "success")
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertGreaterEqual(json.loads(archive.read("summary.json"))["invalidLines"], 3)
            combined = b"".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"SYNTHETIC_HUGE_CANARY", combined)
            self.assertNotIn(b"SYNTHETIC_INCOMPLETE_CANARY", combined)

    async def test_overlong_physical_line_discards_valid_json_tail_but_keeps_next_line(self):
        self.service.record("startup")
        active = self.logs / "diagnostics" / "active.jsonl"
        tail = encoded({"event": "operation_stage", "stage": "closed", "status": "success"})
        following = encoded({"event": "operation_stage", "stage": "unknown", "status": "unknown"})
        for length in (LINE_BYTES, LINE_BYTES + 1, LINE_BYTES * 3 + 7):
            with self.subTest(length=length):
                active.write_bytes(LOG_MARKER + b"X" * length + tail + following)
                result = await self.service.export({})
                self.assertEqual(result["state"], "success")
                with zipfile.ZipFile(result["path"]) as archive:
                    self.assertEqual(archive.read("logs/active.jsonl"), LOG_MARKER + following)
                    self.assertEqual(json.loads(archive.read("summary.json"))["invalidLines"], 1)
        # An unterminated oversized physical line is also counted only once.
        active.write_bytes(LOG_MARKER + b"X" * (LINE_BYTES * 3) + tail.rstrip(b"\n"))
        snapshot, bad = self.service._snapshot()
        self.assertEqual(snapshot["logs/active.jsonl"], LOG_MARKER)
        self.assertEqual(bad, 1)

    async def test_get_operation_successful_polls_do_not_record_diagnostics(self):
        plugin = main.Plugin()
        plugin._diagnostics = self.service
        plugin._flow = InstallFlow(str(self.root), str(self.root / "runtime"), "self", "self", Mock(), Mock(), observer=plugin._diagnostic)
        for _ in range(3):
            self.assertIsNone(await plugin.get_operation())
        plugin._flow.operation = {"id": "b57e4549130141ba8663a5266e940946", "stage": "closed", "saw_finish": False}
        for _ in range(3):
            self.assertEqual((await plugin.get_operation())["stage"], "closed")
        self.assertEqual(self.log_files(), [])
        self.assertFalse(plugin._flow.file.exists())

    async def test_get_operation_read_failures_record_only_safe_failure_fields(self):
        for error in (OSError("SYNTHETIC_READ_SECRET"), json.JSONDecodeError("SYNTHETIC_PARSE_SECRET", "bad", 0), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                plugin = main.Plugin()
                plugin._diagnostics = self.service
                with patch.object(plugin, "_install_flow", side_effect=error), patch.object(main.time, "monotonic", side_effect=[10.0, 10.125]):
                    with self.assertRaises(type(error)) as raised:
                        await plugin.get_operation()
                self.assertIs(raised.exception, error)
        rows = [json.loads(line) for line in self.log_files()[0].read_bytes().splitlines()][1:]
        self.assertEqual(rows, [
            {"event": "rpc_failed", "rpc": "get_operation", "errorType": error_type, "durationMs": 125, "timestamp": row["timestamp"]}
            for row, error_type in zip(rows, ("OSError", "Exception", "CancelledError"))
        ])
        self.assertEqual(len(rows), 3)

    async def test_get_operation_persistence_failures_keep_original_exception_and_order_when_logger_fails(self):
        import install_flow
        for failure in ("binding", "verified", "closed"):
            for logger_fails in (False, True):
                with self.subTest(failure=failure, logger_fails=logger_fails):
                    plugin = main.Plugin()
                    recorded = Mock(side_effect=OSError("SYNTHETIC_LOG_FULL") if logger_fails else None)
                    plugin._diagnostics = types.SimpleNamespace(record=recorded)
                    flow = InstallFlow(str(self.root), str(self.root / f"runtime-{failure}-{logger_fails}"), "self", "self", Mock(), Mock(), observer=plugin._diagnostic)
                    plugin._flow = flow
                    flow.operation = {"id": "a" * 32, "stage": "installing" if failure == "verified" else "verified",
                        "package": {"name": "Example"}, "repo": "acme/example", "previous_stamp": None,
                        "saw_finish": True, "binding_saved": False, "outcome": None}
                    flow._update()
                    before = flow.operation
                    persisted = flow.file.read_bytes()
                    recorded.reset_mock()
                    original_error = OSError("SYNTHETIC_PERSIST_SECRET")
                    order = []
                    def save_binding(*_args):
                        order.append("binding")
                        if failure == "binding":
                            raise original_error
                    def save_result(_path, candidate):
                        order.append(candidate["stage"])
                        raise original_error
                    with patch.object(main, "_settings_path", return_value=str(self.root / "settings.json")), patch.object(main, "save_settings", side_effect=save_binding), patch.object(install_flow, "save_settings", side_effect=save_result), patch.object(install_flow, "verify_installed", return_value=True):
                        with self.assertRaises(OSError) as raised:
                            await plugin.get_operation()
                    self.assertIs(raised.exception, original_error)
                    self.assertEqual(order, ["binding"] if failure == "binding" else ["verified"] if failure == "verified" else ["binding", "closed"])
                    self.assertIs(flow.operation, before)
                    self.assertEqual(flow.file.read_bytes(), persisted)
                    self.assertFalse(flow.operation["binding_saved"])
                    self.assertIsNone(flow.operation["outcome"])
                    recorded.assert_called_once()
                    event, = recorded.call_args.args
                    self.assertEqual(event, "rpc_failed")
                    fields = recorded.call_args.kwargs
                    self.assertEqual(set(fields), {"rpc", "durationMs", "errorType"})
                    self.assertEqual(fields["rpc"], "get_operation")
                    self.assertEqual(fields["errorType"], "OSError")
                    self.assertGreaterEqual(fields["durationMs"], 0)

    async def test_foreign_directory_entry_limit_fails_without_deleting_anything(self):
        self.service.record("startup")
        self.export_dir.mkdir(parents=True)
        for n in range(513):
            (self.export_dir / f"foreign-{n}").touch()
        self.assertEqual((await self.service.export({}))["state"], "error")
        self.assertEqual(len(list(self.export_dir.iterdir())), 513)


    async def test_rotation_and_export_snapshot_are_serialized_under_concurrent_writes(self):
        def write():
            for n in range(300):
                self.service.record("rpc_end", rpc="list_releases", count=n)
        with patch.object(module, "LOG_BYTES", 2048):
            self.service.record("startup")
            _, result = await asyncio.gather(asyncio.to_thread(write), self.service.export({}))
            self.assertEqual(result["state"], "success")
            self.assertLessEqual(sum(p.stat().st_size for p in self.log_files()), 4 * 2048)
            with zipfile.ZipFile(result["path"]) as archive:
                for name in archive.namelist():
                    if name.endswith(".jsonl"):
                        data = archive.read(name)
                        self.assertLessEqual(len(data), 2048)
                        for line in data.splitlines():
                            json.loads(line)


if __name__ == "__main__":
    unittest.main()
