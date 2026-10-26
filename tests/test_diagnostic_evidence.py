import asyncio
import errno
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
import urllib.error
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "backend"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
from sideload.diagnostic_context import CONTEXT, error_fields, identifier
from sideload.diagnostics import Diagnostics
from sideload.diagnostic_snapshot import library_snapshot
from sideload.install_flow import InstallFlow
from sideload.persist import save_settings
from sideload.rpc import _observed_rpc


class DiagnosticEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.settings = self.root / "settings"
        save_settings(str(self.settings / "settings.json"), {"networkMode": "auto", "autoCheck": False, "showHomeTaskNotice": False, "downloadTimeoutMinutes": 5,
            "bindings": {"SYNTHETIC_TOKEN": {"repo": "secret/repository"}}})
        self.context = patch.multiple(main.decky, create=True, DECKY_PLUGIN_SETTINGS_DIR=str(self.settings), DECKY_PLUGIN_RUNTIME_DIR=str(self.runtime))
        self.context.start()
        self.addCleanup(self.context.stop)
        self.service = Diagnostics(str(self.root / "logs"), str(self.root / "home"), {"backend": "0.3.0"})
        self.plugin = main.Plugin()
        self.plugin._diagnostics = self.service

    async def asyncTearDown(self):
        await self.service.close()

    async def exported(self):
        result = await self.plugin.export_diagnostics()
        self.assertEqual(result["state"], "success")
        with zipfile.ZipFile(result["path"]) as archive:
            summary = json.loads(archive.read("summary.json"))
            rows = [json.loads(line) for name in archive.namelist() if name.endswith(".jsonl") for line in archive.read(name).splitlines()]
            payload = b"".join(archive.read(name) for name in archive.namelist())
        return summary, rows, payload

    async def test_export_reads_interrupted_operation_without_recovering_or_leaking_private_fields(self):
        path = self.runtime / "installations/operation.json"
        operation = {"id": "a" * 32, "stage": "installing", "repo": "secret/repository", "tag": "SYNTHETIC_TOKEN", "request_id": "private-loader-request",
            "network_mode": "auto", "target": {"name": "SYNTHETIC_TOKEN", "version": "1.2.3", "path": "private/subdirectory"},
            "saw_start": True, "saw_finish": False, "binding_saved": False, "error": "Bearer SYNTHETIC_TOKEN /home/private",
            "diagnostic_error": {"errorCode": "disk_full", "errno": errno.ENOSPC, "message": "SYNTHETIC_TOKEN"},
            "dependency_download": {"status": "failed", "route": "gh-proxy", "attempt": 2, "received": 42, "total": 99, "name": "SYNTHETIC_TOKEN"},
            "backup": {"files": 3, "bytes": 42, "archive": "/home/private/data.zip", "sha256": "b" * 64}, "observations": [{"id": "installing", "status": "failed", "detail": "SYNTHETIC_TOKEN"}]}
        save_settings(str(path), operation)
        original = path.read_bytes()
        with patch.object(self.plugin, "_install_flow", side_effect=AssertionError("must not recover")):
            summary, _, payload = await self.exported()
        snapshot = summary["details"]["operation"]
        self.assertEqual(snapshot["stage"], "installing")
        self.assertTrue(snapshot["sawStart"])
        self.assertFalse(snapshot["sawFinish"])
        self.assertNotIn("outcome", snapshot)
        self.assertEqual(snapshot["error"]["errorCode"], "disk_full")
        self.assertEqual(snapshot["dependency"]["received"], 42)
        self.assertEqual(snapshot["backup"]["files"], 3)
        self.assertEqual(snapshot["pluginId"], identifier("plugin", "SYNTHETIC_TOKEN"))
        self.assertFalse(summary["details"]["settings"]["showHomeTaskNotice"])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(hasattr(self.plugin, "_flow"))
        for secret in (b"SYNTHETIC_TOKEN", b"secret/repository", b"/home/private", b"private-loader-request", b"private/subdirectory"):
            self.assertNotIn(secret, payload)

    async def test_distinct_os_and_network_failures_preserve_codes_not_exception_text(self):
        cases = [(PermissionError(errno.EACCES, "SYNTHETIC_TOKEN"), "permission_denied"), (OSError(errno.ENOSPC, "SYNTHETIC_TOKEN"), "disk_full"),
            (urllib.error.URLError(socket.gaierror(-2, "SYNTHETIC_TOKEN")), "dns_failed"), (urllib.error.URLError(TimeoutError("SYNTHETIC_TOKEN")), "network_timeout"),
            (urllib.error.HTTPError("https://secret.invalid?token=SYNTHETIC_TOKEN", 429, "secret", {}, None), "rate_limited")]
        for error, code in cases:
            wrapped = RuntimeError("SYNTHETIC_TOKEN")
            wrapped.__cause__ = error
            fields = error_fields(wrapped)
            self.assertEqual(fields["errorCode"], code)
            self.service.record("rpc_failed", rpc="prepare_install", **fields)
            if isinstance(error, urllib.error.HTTPError): error.close()
        try:
            await self.plugin.probe_repository("SYNTHETIC_TOKEN")
        except RuntimeError:
            pass
        _, rows, payload = await self.exported()
        failure = next(row for row in rows if row.get("rpc") == "probe_repository" and row["event"] == "rpc_failed")
        self.assertEqual(failure["errorCode"], "invalid_input")
        self.assertTrue(any(frame["module"] == "releases_api" for frame in failure["frames"]))
        self.assertNotIn(b"SYNTHETIC_TOKEN", payload)
        self.assertNotIn(b"https://", payload)

    async def test_concurrent_rpc_requests_and_worker_events_keep_separate_parent_chains(self):
        service = self.service
        class API:
            def _diagnostic(self, event, **fields): service.record(event, **fields)
            @_observed_rpc
            async def search_repositories(self):
                await asyncio.sleep(0)
                await self.get_app_settings()
            @_observed_rpc
            async def get_app_settings(self):
                await asyncio.to_thread(service.record, "network_attempt", route="direct", status="ok")
        await asyncio.gather(API().search_repositories(), API().search_repositories())
        self.assertEqual(CONTEXT.get(), {})
        _, rows, _ = await self.exported()
        parents = [row for row in rows if row.get("rpc") == "search_repositories" and row["event"] == "rpc_start"]
        self.assertEqual(len({row["requestId"] for row in parents}), 2)
        children = [row for row in rows if row.get("rpc") == "get_app_settings" and row["event"] == "rpc_start"]
        self.assertEqual({row["parentId"] for row in children}, {row["requestId"] for row in parents})
        self.assertEqual({row["requestId"] for row in rows if row["event"] == "network_attempt"}, {row["requestId"] for row in children})

    async def test_dependency_failure_and_cleanup_are_exported_with_task_evidence(self):
        flow = InstallFlow(str(self.root / "home"), str(self.runtime), "self", "self", None, None, observer=self.plugin._diagnostic)
        flow.operation = {"id": "c" * 32, "stage": "checking", "download": {"status": "complete", "route": "direct", "attempt": 1}, "saw_start": False, "saw_finish": False}
        flow._dependency_progress("c" * 32, flow.download_cancel, {"status": "downloading", "route": "gh-proxy", "attempt": 2, "index": 1, "count": 1, "received": 42, "total": 99, "name": "SYNTHETIC_TOKEN"})
        flow._update(stage="failed", error="SYNTHETIC_TOKEN", diagnostic_error=error_fields(OSError(errno.ENOSPC, "SYNTHETIC_TOKEN")))
        self.plugin._flow = flow
        expired = self.runtime / "installations" / ("d" * 32) / "data-backup.zip"
        expired.parent.mkdir(parents=True)
        expired.write_bytes(b"do not export backup contents")
        os.utime(expired, (1, 1))
        await flow.clean_backups()
        summary, rows, payload = await self.exported()
        dependency = [row for row in rows if row["event"] == "dependency_stage"]
        self.assertEqual([row["status"] for row in dependency], ["downloading", "failed"])
        self.assertTrue(all(row["operationId"] == "c" * 32 for row in dependency))
        self.assertEqual(summary["details"]["operation"]["error"]["errorCode"], "disk_full")
        self.assertEqual(summary["health"]["cleanup"]["deleted"], 1)
        self.assertFalse(expired.exists())
        self.assertNotIn(b"SYNTHETIC_TOKEN", payload)
        self.assertNotIn(b"do not export backup contents", payload)

    async def test_cached_library_health_and_loaded_frontend_identity_remain_distinct(self):
        self.plugin._diagnostic_library = library_snapshot([{"name": "SYNTHETIC_TOKEN", "version": "1.2.3", "loader_managed": True,
            "ownership": {"state": "owned", "eligible": True, "intent": {"id": "a" * 32, "state": "active", "prior_frozen": False}}}], {"ok": True}, {"source": "cache", "warning": None, "stale": False}, {"SYNTHETIC_TOKEN"})
        self.service.builds = {"backend": "a" * 64, "installedFrontend": "b" * 64}
        self.service.record_batch([{"event": "frontend_build", "fingerprint": "c" * 64, "version": "0.2.0", "clientId": "c" * 32}])
        summary, _, payload = await self.exported()
        self.assertTrue(summary["details"]["library"]["loaderOk"])
        self.assertTrue(summary["details"]["library"]["catalogOk"])
        self.assertTrue(summary["details"]["library"]["plugins"][0]["frozen"])
        self.assertFalse(summary["builds"]["matchesInstalled"])
        self.assertEqual(summary["versions"]["frontend"], "0.2.0")
        self.assertEqual(summary["versions"]["backend"], "0.3.0")
        self.service.record_batch([{"event": "frontend_build", "fingerprint": "b" * 64, "version": "0.3.0", "clientId": "c" * 32}])
        updated, _, _ = await self.exported()
        self.assertTrue(updated["builds"]["matchesInstalled"])
        self.assertNotIn(b"SYNTHETIC_TOKEN", payload)

    async def test_corrupt_task_can_still_be_exported_without_loading_it(self):
        path = self.runtime / "installations/operation.json"
        path.parent.mkdir()
        path.write_text("not-json SYNTHETIC_TOKEN")
        original = path.read_bytes()
        summary, _, payload = await self.exported()
        self.assertFalse(summary["details"]["operation"]["available"])
        self.assertEqual(summary["details"]["operation"]["error"]["errorCode"], "invalid_response")
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn(b"SYNTHETIC_TOKEN", payload)

    async def test_sdk_records_new_modules_and_classifies_errors_without_original_text(self):
        logger = logging.Logger("diagnostic-sdk")
        path = self.root / "sdk.log"
        original = logging.FileHandler(path)
        logger.addHandler(original)
        self.service.attach_sdk(logger, str(path))
        record = logging.LogRecord("diagnostic-sdk", logging.ERROR, str(ROOT / "backend/sideload/releases_api.py"), 100, "SYNTHETIC_TOKEN", (), (PermissionError, PermissionError(errno.EACCES, "SYNTHETIC_TOKEN"), None))
        logger.handle(record)
        _, rows, payload = await self.exported()
        event = next(row for row in rows if row["event"] == "sdk_log")
        self.assertEqual(event["module"], "releases_api")
        self.assertEqual(event["errorCode"], "permission_denied")
        self.assertEqual(event["errno"], errno.EACCES)
        self.assertEqual(path.read_bytes(), b"")
        self.assertNotIn(b"SYNTHETIC_TOKEN", payload)

    async def test_export_observes_frontend_replacement_without_relabeling_running_backend(self):
        import hashlib
        installed = self.root / "installed"
        (installed / "dist").mkdir(parents=True)
        (installed / "py_modules/sideload").mkdir(parents=True)
        (installed / "main.py").write_text("# fixture source\n")
        bundle = installed / "dist/index.js"
        metadata = installed / "dist/build.json"
        bundle.write_text("first frontend")
        metadata.write_text(json.dumps({"fingerprint": "b" * 64, "bundle": hashlib.sha256(bundle.read_bytes()).hexdigest()}))
        self.service.builds = {"backend": "a" * 64}
        self.service.record_batch([{"event": "frontend_build", "fingerprint": "b" * 64, "version": "0.3.0", "clientId": "c" * 32}])
        with patch.object(main.decky, "DECKY_PLUGIN_DIR", str(installed), create=True):
            first, _, _ = await self.exported()
            self.assertTrue(first["builds"]["matchesInstalled"])
            bundle.write_text("replacement frontend")
            metadata.write_text(json.dumps({"fingerprint": "d" * 64, "bundle": hashlib.sha256(bundle.read_bytes()).hexdigest()}))
            changed, _, _ = await self.exported()
            self.assertFalse(changed["builds"]["matchesInstalled"])
            self.assertEqual(changed["builds"]["backend"], "a" * 64)
            self.assertNotEqual(first["builds"]["installedBundle"], changed["builds"]["installedBundle"])
            metadata.unlink()
            unknown, _, _ = await self.exported()
            self.assertNotIn("matchesInstalled", unknown["builds"])
