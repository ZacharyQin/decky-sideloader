import asyncio
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))
import install_flow
from install_flow import InstallFlow
from test_install_ops import make_package
from test_transport import trickling_response
import github_transport as network
from install_ops import download_asset

URL = "https://github.com/acme/example/releases/download/v2/example.zip"


class InstallFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = make_package(self.home / "source.zip")
        self.releases = Mock(return_value=[{"tag": "v2", "repo": "acme/example", "zips": [{"name": "example.zip", "url": URL}]}])
        self.download = patch.object(install_flow, "download_asset", side_effect=lambda _url, path, _ctx, **_kwargs: shutil.copyfile(self.source, path))
        self.download.start()
        self.addCleanup(self.download.stop)
        self.flow = self.new_flow()
        self.addAsyncCleanup(self.flow.unload)

    def new_flow(self):
        return InstallFlow(str(self.home), str(self.home / "runtime"), "GitHub Sideload", "github-sideload", self.releases, lambda: None)

    async def prepared(self, existing=None):
        await self.flow.prepare("acme/example", "v2", URL, existing)
        await self.flow.task
        return await self.flow.status()

    def install_old(self, version="1.0.0"):
        old = make_package(self.home / "old.zip", version=version)
        with ZipFile(old) as archive:
            archive.extractall(self.home / "plugins")
        directory = self.home / "settings" / "example"
        directory.mkdir(parents=True)
        (directory / "config.json").write_text('{"keep":true}')

    async def test_progress_callbacks_are_serialized_on_loop_and_old_ids_cannot_write(self):
        import threading
        loop_thread = threading.get_ident()
        threads = []
        original_update = self.flow._update
        def update(**changes):
            threads.append(threading.get_ident())
            original_update(**changes)
        def download(_url, path, _ctx, *, progress, **_kwargs):
            for size in (1, 2, 3):
                progress({"received": size, "total": None, "status": "downloading", "route": "direct", "attempt": 1})
            shutil.copyfile(self.source, path)
            progress({"received": 3, "total": None, "status": "complete", "route": "direct", "attempt": 1})
        with patch.object(self.flow, "_update", side_effect=update), patch.object(install_flow, "download_asset", side_effect=download):
            op = await self.prepared()
        self.assertEqual(op["download"]["received"], 3)
        self.assertEqual(op["download"]["status"], "complete")
        self.assertTrue(all(t == loop_thread for t in threads))
        old_id = op["id"]
        self.flow._update(id="new-task", stage="checking", download=None)
        before = self.flow.file.read_bytes()
        self.flow._download_progress(old_id, threading.Event(), {"received": 999})
        self.assertEqual(self.flow.file.read_bytes(), before)
        self.assertIsNone(self.flow.operation["download"])
        self.flow._update(stage="failed")
        before = self.flow.file.read_bytes()
        self.flow._download_progress("new-task", threading.Event(), {"received": 999})
        self.assertEqual(self.flow.file.read_bytes(), before)

    async def test_unload_stops_worker_and_late_progress_cannot_change_failed_or_reloaded_task(self):
        import threading
        started = threading.Event()
        callbacks = []
        def download(_url, _path, _ctx, *, progress, cancel, **_kwargs):
            callbacks.append(progress)
            progress({"received": 10, "total": None, "status": "downloading", "route": "direct", "attempt": 1})
            started.set()
            if not cancel.wait(2):
                raise AssertionError("unload did not signal cancellation")
            progress({"received": 999, "status": "complete"})
            raise RuntimeError("stopped")
        with patch.object(install_flow, "download_asset", side_effect=download):
            await self.flow.prepare("acme/example", "v2", URL, None)
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            await asyncio.sleep(0)
            await self.flow.unload()
        self.assertTrue(self.flow.task.done())
        op = await self.flow.status()
        self.assertEqual(op["stage"], "failed")
        self.assertEqual(op["download"]["status"], "failed")
        self.assertEqual(op["download"]["received"], 10)
        before = self.flow.file.read_bytes()
        callbacks[0]({"received": 1000, "status": "complete"})
        await asyncio.sleep(0)
        self.assertEqual(self.flow.file.read_bytes(), before)
        self.assertEqual((await self.new_flow().status())["download"]["received"], 10)

    async def test_new_install_matches_probed_plugin_and_package_names(self):
        target = {"path": "plugins/example", "name": "Example", "package_name": "example", "tree_sha": "a" * 40}
        make_package(self.source, extra={"example/package.json": json.dumps({"name": "other-plugin", "version": "2.0.0"})})
        await self.flow.prepare("acme/example", "v2", URL, None, target)
        await self.flow.task
        failed = await self.flow.status()
        self.assertEqual(failed["stage"], "failed")
        self.assertIn("身份", failed["error"])
        self.assertIsNone(failed["backup"])
        self.assertIsNone(failed["request_id"])
        make_package(self.source, extra={"example/package.json": json.dumps({"name": "example", "version": "2.0.0"})})
        await self.flow.prepare("acme/example", "v2", URL, None, target)
        await self.flow.task
        ready = await self.flow.status()
        self.assertEqual(ready["stage"], "ready")
        self.assertEqual(ready["target"], target)

    async def test_new_install_rejects_another_plugin_in_the_same_monorepo(self):
        target = {"path": "plugins/second", "name": "Second", "package_name": "example"}
        make_package(self.source, extra={"example/package.json": json.dumps({"name": "example", "version": "2.0.0"})})
        await self.flow.prepare("acme/example", "v2", URL, None, target)
        await self.flow.task
        self.assertEqual((await self.flow.status())["stage"], "failed")

    async def test_prepare_never_installs_and_can_be_resumed_after_reload(self):
        op = await self.prepared()
        self.assertEqual(op["stage"], "ready")
        self.assertFalse((self.home / "plugins" / "example").exists())
        restarted = self.new_flow()
        self.assertEqual((await restarted.status())["stage"], "ready")
        self.assertNotIn("hashes", op["package"])

    async def test_artifact_uses_loader_raw_file_path_with_spaces(self):
        flow = InstallFlow(str(self.home), str(self.home / "runtime with spaces"), "GitHub Sideload", "github-sideload", self.releases, lambda: None)
        await flow.prepare("acme/example", "v2", URL, None)
        await flow.task
        result = await flow.status()
        self.assertEqual(result["stage"], "ready")
        self.assertTrue(Path(result["artifact"][7:]).is_file())

    async def test_rejects_unrelated_release_asset(self):
        await self.flow.prepare("acme/example", "v2", URL + "different", None)
        await self.flow.task
        self.assertEqual((await self.flow.status())["stage"], "failed")

    async def test_submit_is_one_shot_and_preserves_hash(self):
        op = await self.prepared()
        submitted = await self.flow.submit(op["id"])
        self.assertEqual(submitted["stage"], "awaiting")
        self.assertTrue(submitted["artifact"].startswith("file://"))
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"])
        self.assertFalse((self.home / "plugins" / "example").exists())

    async def test_backup_failure_prevents_submission(self):
        self.install_old()
        with patch.object(install_flow, "backup_data", side_effect=PermissionError("cannot read settings")):
            op = await self.prepared("Example")
        self.assertEqual(op["stage"], "failed")
        self.assertIn("cannot read", op["error"])
        self.assertEqual(json.loads((self.home / "plugins" / "example" / "package.json").read_text())["version"], "1.0.0")
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"])

    async def test_archive_changed_after_prepare_is_rejected(self):
        op = await self.prepared()
        (Path(op["directory"]) / "plugin.zip").write_bytes(b"tampered")
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"])
        self.assertEqual((await self.flow.status())["stage"], "failed")

    async def test_existing_plugin_change_blocks_submission(self):
        self.install_old()
        op = await self.prepared("Example")
        self.assertEqual(op["backup"]["files"], 1)
        (self.home / "plugins" / "example" / "package.json").write_text('{"version":"9.0.0"}')
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"])

    async def test_finish_event_alone_never_means_success(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual((await self.flow.status())["stage"], "unknown")

    async def test_success_requires_correlated_event_and_matching_installed_files(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        await self.flow.event(op["id"], "prompt", "Unrelated", "wrong", "0" * 64)
        await self.flow.event(op["id"], "finish", "Example")
        self.assertFalse((await self.flow.status())["saw_finish"])
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "start", "Example")
        with ZipFile(self.source) as archive:
            archive.extractall(self.home / "plugins")
        self.assertEqual((await self.flow.status())["stage"], "installing")
        await self.flow.event(op["id"], "finish", "Unrelated")
        self.assertFalse((await self.flow.status())["saw_finish"])
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual((await self.flow.status())["stage"], "verified")

    async def test_same_version_is_not_verified_without_actual_replacement(self):
        self.install_old("2.0.0")
        op = await self.prepared("Example")
        await self.flow.submit(op["id"])
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual((await self.flow.status())["stage"], "unknown")

    async def test_unknown_does_not_automatically_replay(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        self.flow._update(submitted_at=0)
        self.assertEqual((await self.flow.status())["stage"], "unknown")
        with self.assertRaises(RuntimeError):
            await self.flow.prepare("acme/example", "v2", URL, None)
        await self.flow.close(op["id"])
        self.assertEqual((await self.flow.status())["stage"], "closed")

    async def test_confirmed_cancellation_is_not_installation_success(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "cancelled", "Example")
        self.assertEqual((await self.flow.status())["stage"], "cancelled")

    async def test_new_install_does_not_overwrite_unrecognized_directory(self):
        (self.home / "plugins" / "example").mkdir(parents=True)
        op = await self.prepared()
        self.assertEqual(op["stage"], "failed")
        self.assertIn("目录已存在", op["error"])

    async def verified_operation(self):
        if (self.home / "plugins").exists():
            shutil.rmtree(self.home / "plugins")
        op = await self.prepared()
        await self.flow.submit(op["id"])
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "start", "Example")
        with ZipFile(self.source) as archive:
            archive.extractall(self.home / "plugins")
        await self.flow.event(op["id"], "finish", "Example")
        return op

    async def test_finalize_returns_fresh_closed_success_and_retains_backup(self):
        op = await self.verified_operation()
        bind = Mock()
        result = await self.flow.status(bind)
        self.assertEqual(result["stage"], "closed")
        self.assertEqual(result["outcome"], "success")
        self.assertTrue(result["binding_saved"])
        self.assertEqual(result["close_reason"], "verified")
        self.assertTrue(result["completed_at"])
        self.assertEqual(result["directory"], op["directory"])
        self.assertEqual(json.loads(self.flow.file.read_text()), self.flow.operation)
        bind.assert_called_once()
        self.assertEqual((await self.new_flow().status())["outcome"], "success")
        await self.flow.status(bind)
        bind.assert_called_once()

    async def test_binding_failure_keeps_verified_active_and_retries_only_finalization(self):
        op = await self.verified_operation()
        with self.assertRaisesRegex(OSError, "binding disk full"):
            await self.flow.status(Mock(side_effect=OSError("binding disk full")))
        self.assertEqual(self.flow.operation["stage"], "verified")
        self.assertFalse(self.flow.operation["binding_saved"])
        with self.assertRaises(RuntimeError):
            await self.flow.close(op["id"])
        with self.assertRaises(RuntimeError):
            await self.flow.prepare("acme/example", "v2", URL, None)
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"])
        self.assertEqual((await self.new_flow().status(Mock()))["stage"], "closed")

    async def test_failed_verified_or_success_persistence_never_changes_memory_to_success(self):
        for fail_stage in ("verified", "closed"):
            with self.subTest(stage=fail_stage):
                self.flow = self.new_flow()
                if self.flow.operation:
                    self.flow._update(stage="closed")
                await self.verified_operation()
                save = install_flow.save_settings
                def failing_save(path, candidate):
                    if candidate["stage"] == fail_stage:
                        raise OSError("result write failed")
                    save(path, candidate)
                bind = Mock()
                with patch.object(install_flow, "save_settings", side_effect=failing_save), self.assertRaises(OSError):
                    await self.flow.status(bind)
                self.assertNotEqual(self.flow.operation["stage"], "closed")
                self.assertFalse(self.flow.operation["binding_saved"])
                self.assertIsNone(self.flow.operation["outcome"])
                self.assertEqual(bind.call_count, int(fail_stage == "closed"))
                self.assertEqual((await self.new_flow().status(Mock()))["stage"], "closed")

    async def test_main_get_operation_persists_binding_before_returning_latest_closed_snapshot(self):
        import main
        op = await self.verified_operation()
        plugin = main.Plugin()
        plugin._flow = self.flow
        settings_path = str(self.home / "self-settings.json")
        with patch.object(main, "_settings_path", return_value=settings_path):
            with patch.object(main, "save_settings", side_effect=OSError("binding write failed")), self.assertRaises(OSError):
                await plugin.get_operation()
            self.assertFalse(self.flow.operation["binding_saved"])
            with self.assertRaises(RuntimeError):
                plugin._require_binding_idle("Example")
            result = await plugin.get_operation()
            self.assertEqual(result["id"], op["id"])
            self.assertEqual(result["stage"], "closed")
            self.assertTrue(result["binding_saved"])
            self.assertEqual(main.load_settings(settings_path)["bindings"]["Example"]["repo"], "acme/example")
            with patch.object(main, "save_settings") as save:
                await plugin.get_operation()
            save.assert_not_called()

    async def test_reinstall_final_success_preserves_backup_and_standard_data(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        shutil.rmtree(self.home / "plugins" / "example")
        await self.flow.submit(op["id"], after_uninstall=True)
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "start", "Example")
        with ZipFile(self.source) as archive:
            archive.extractall(self.home / "plugins")
        await self.flow.event(op["id"], "finish", "Example")
        result = await self.flow.status(Mock())
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["backup"], op["backup"])
        self.assertTrue(Path(result["backup"]["archive"]).is_file())
        self.assertTrue((self.home / "settings" / "example" / "config.json").is_file())

    async def test_manual_close_and_legacy_closed_without_evidence_never_become_success(self):
        op = await self.prepared()
        self.flow._update(stage="unknown", saw_finish=False)
        with patch.object(install_flow, "save_settings", side_effect=OSError("disk full")), self.assertRaises(OSError):
            await self.flow.close(op["id"])
        self.assertEqual(self.flow.operation["stage"], "unknown")
        await self.flow.close(op["id"])
        bind = Mock()
        result = await self.new_flow().status(bind)
        self.assertEqual(result["close_reason"], "manual")
        self.assertIsNone(result["outcome"])
        self.assertFalse(result["binding_saved"])
        bind.assert_not_called()

    async def test_file_match_without_start_and_finish_remains_unknown(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        with ZipFile(self.source) as archive:
            archive.extractall(self.home / "plugins")
        await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual((await self.flow.status(Mock()))["stage"], "unknown")
        self.assertFalse(self.flow.operation["saw_finish"])

    async def test_same_name_conflicting_request_and_dispatch_error_block_success(self):
        for conflict in ("request", "hash", "dispatch"):
            with self.subTest(conflict=conflict):
                if self.flow.operation:
                    self.flow._update(stage="closed")
                op = await self.verified_operation()
                if conflict == "dispatch":
                    await self.flow.event(op["id"], "dispatch_error", "Example", "failed")
                else:
                    await self.flow.event(op["id"], "prompt", "Example", "request-2" if conflict == "request" else "request-1", "b" * 64 if conflict == "hash" else op["package"]["sha256"])
                self.assertEqual(self.flow.operation["request_id"], "request-1")
                bind = Mock()
                self.assertEqual((await self.flow.status(bind))["stage"], "unknown")
                bind.assert_not_called()

    async def test_duplicate_events_are_idempotent_and_old_task_cannot_change_new_task(self):
        op = await self.verified_operation()
        for _ in range(2):
            await self.flow.event(op["id"], "prompt", "Example", "request-1", op["package"]["sha256"])
            await self.flow.event(op["id"], "finish", "Example")
        await self.flow.status(Mock())
        new = await self.prepared("Example")
        before = self.flow.file.read_bytes()
        await self.flow.event(op["id"], "start", "Example")
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual(self.flow.file.read_bytes(), before)
        self.assertNotEqual(new["id"], op["id"])

    async def prepared_reinstall(self):
        self.install_old()
        await self.flow.prepare("acme/example", "v2", URL, "Example", mode="reinstall")
        await self.flow.task
        return await self.flow.status()

    async def test_reinstall_claim_once_then_absence_recheck_before_install(self):
        op = await self.prepared_reinstall()
        self.assertEqual(op["backup"]["files"], 1)
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"])
        claimed = await self.flow.claim_uninstall(op["id"])
        self.assertEqual(claimed["stage"], "uninstalling")
        with self.assertRaises(RuntimeError):
            await self.flow.claim_uninstall(op["id"])
        # Simulate the Loader, never product code, deleting only program files.
        shutil.rmtree(self.home / "plugins" / "example")
        result = await self.flow.submit(op["id"], after_uninstall=True)
        self.assertEqual(result["stage"], "awaiting")
        self.assertEqual((self.home / "settings" / "example" / "config.json").read_text(), '{"keep":true}')
        self.assertTrue(Path(op["backup"]["archive"]).is_file())
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"], after_uninstall=True)

    async def test_reinstall_missing_backup_or_changed_original_blocks_uninstall_claim(self):
        for change in ("backup", "package", "original"):
            with self.subTest(change=change):
                if self.flow.operation:
                    self.flow._update(stage="closed")
                if (self.home / "plugins").exists():
                    shutil.rmtree(self.home / "plugins")
                    shutil.rmtree(self.home / "settings")
                op = await self.prepared_reinstall()
                target = Path(op["backup"]["archive"]) if change == "backup" else Path(op["directory"]) / "plugin.zip" if change == "package" else self.home / "plugins" / "example" / "package.json"
                target.write_bytes(b"changed")
                with self.assertRaises(RuntimeError):
                    await self.flow.claim_uninstall(op["id"])
                self.assertEqual(self.flow.operation["stage"], "failed")

    async def test_reinstall_still_present_reoccupied_or_tampered_blocks_install(self):
        for change in ("present", "symlink", "package", "backup", "duplicate"):
            with self.subTest(change=change):
                if self.flow.operation:
                    self.flow._update(stage="closed")
                if (self.home / "plugins").exists():
                    shutil.rmtree(self.home / "plugins")
                    shutil.rmtree(self.home / "settings")
                op = await self.prepared_reinstall()
                await self.flow.claim_uninstall(op["id"])
                if change != "present":
                    shutil.rmtree(self.home / "plugins" / "example")
                if change == "symlink":
                    (self.home / "plugins" / "example").symlink_to(self.home / "absent")
                elif change in ("package", "backup"):
                    target = Path(op["backup"]["archive"]) if change == "backup" else Path(op["directory"]) / "plugin.zip"
                    target.write_bytes(b"changed")
                elif change == "duplicate":
                    folder = self.home / "plugins" / "other"
                    folder.mkdir()
                    (folder / "plugin.json").write_text('{"name":"Example"}')
                    (folder / "package.json").write_text('{"version":"1"}')
                with self.assertRaises(RuntimeError):
                    await self.flow.submit(op["id"], after_uninstall=True)
                self.assertEqual(self.flow.operation["stage"], "unknown")

    async def test_reinstall_cancel_error_timeout_and_reload_never_continue(self):
        for stop in ("cancel", "error", "timeout", "reload"):
            with self.subTest(stop=stop):
                if self.flow.operation:
                    self.flow._update(stage="closed")
                if (self.home / "plugins").exists():
                    shutil.rmtree(self.home / "plugins")
                    shutil.rmtree(self.home / "settings")
                op = await self.prepared_reinstall()
                if stop == "cancel":
                    await self.flow.event(op["id"], "uninstall_cancelled", "Example")
                else:
                    await self.flow.claim_uninstall(op["id"])
                    if stop == "error":
                        await self.flow.event(op["id"], "uninstall_error", "Example", "failure")
                    elif stop == "timeout":
                        self.flow._update(uninstall_claimed_at=0)
                        await self.flow.status()
                    else:
                        self.flow = self.new_flow()
                self.assertIn(self.flow.operation["stage"], ("unknown", "cancelled"))
                with self.assertRaises(RuntimeError):
                    await self.flow.submit(op["id"], after_uninstall=True)
                with self.assertRaises(RuntimeError):
                    await self.flow.claim_uninstall(op["id"])

    async def test_unload_interrupts_real_chunk_framing_and_waits_for_writer(self):
        started = threading.Event()
        worker_finished = threading.Event()
        original_download = download_asset
        def download(*args, **kwargs):
            try:
                original_download(*args, **kwargs)
            finally:
                worker_finished.set()
        prefix = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;extension="
        with trickling_response(prefix, ready=started), patch.object(install_flow, "download_asset", side_effect=download):
            await self.flow.prepare("acme/example", "v2", URL, None)
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            start = time.monotonic()
            await asyncio.wait_for(self.flow.unload(), 0.5)
            self.assertLess(time.monotonic() - start, 0.4)
            self.assertTrue(worker_finished.is_set())
            self.assertTrue(self.flow.task.done())
            op = await self.flow.status()
            self.assertEqual(op["stage"], "failed")
            artifact = Path(op["directory"]) / "plugin.zip"
            self.assertEqual(artifact.read_bytes(), b"")
            before = self.flow.file.read_bytes()
            await asyncio.sleep(0.04)
            self.assertEqual(artifact.read_bytes(), b"")
            self.assertEqual(self.flow.file.read_bytes(), before)

    async def test_unload_during_stalled_dns_leaves_no_download_writer_or_late_file_writes(self):
        entered, release, resolved, worker_finished = (threading.Event() for _ in range(4))
        def resolver(host, port, **kwargs):
            self.assertEqual((host, port), ("github.com", 443))
            entered.set()
            release.wait(2)
            resolved.set()
            return []
        def download(*args, **kwargs):
            try:
                download_asset(*args, **kwargs)
            finally:
                worker_finished.set()
        try:
            with patch.object(network.socket, "getaddrinfo", side_effect=resolver), patch.object(install_flow, "download_asset", side_effect=download):
                await self.flow.prepare("acme/example", "v2", URL, None)
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                await asyncio.wait_for(self.flow.unload(), 0.5)
                self.assertTrue(worker_finished.is_set())
                self.assertTrue(self.flow.task.done())
                op = await self.flow.status()
                self.assertEqual(op["stage"], "failed")
                artifact = Path(op["directory"]) / "plugin.zip"
                before = (artifact.read_bytes(), self.flow.file.read_bytes())
                release.set()
                self.assertTrue(await asyncio.to_thread(resolved.wait, 1))
                await asyncio.sleep(0.04)
                self.assertEqual((artifact.read_bytes(), self.flow.file.read_bytes()), before)
        finally:
            release.set()


    async def test_task52_cancel_ready_is_durable_exact_id_and_cannot_submit_after_reload(self):
        op = await self.prepared()
        with self.assertRaises(RuntimeError):
            await self.flow.cancel("old-id")
        with patch.object(install_flow, "save_settings", side_effect=OSError("disk full")), self.assertRaises(OSError):
            await self.flow.cancel(op["id"])
        self.assertFalse(self.flow.operation.get("cancel_requested"))
        self.assertFalse(self.flow.download_cancel.is_set())
        result = await self.flow.cancel(op["id"])
        self.assertEqual(result["stage"], "cancelled")
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(await self.flow.cancel(op["id"]), result)
        reloaded = self.new_flow()
        with self.assertRaises(RuntimeError):
            await reloaded.submit(op["id"])
        new = await self.prepared()
        before = self.flow.file.read_bytes()
        with self.assertRaises(RuntimeError):
            await self.flow.cancel(op["id"])
        self.assertEqual(self.flow.file.read_bytes(), before)
        self.assertNotEqual(new["id"], op["id"])

    async def test_task52_cancel_release_and_backup_waits_real_thread_before_terminal_or_new_task(self):
        for phase in ("release", "backup", "inspect"):
            with self.subTest(phase=phase):
                if self.flow.operation:
                    self.flow._update(stage="closed")
                if (self.home / "plugins").exists():
                    shutil.rmtree(self.home / "plugins")
                    shutil.rmtree(self.home / "settings")
                self.install_old()
                started, release, finished = (threading.Event() for _ in range(3))
                func = self.releases if phase == "release" else getattr(install_flow, "backup_data" if phase == "backup" else "inspect_archive")
                def blocking(*args, **kwargs):
                    started.set()
                    release.wait(2)
                    try:
                        return func(*args, **kwargs)
                    finally:
                        finished.set()
                scope = patch.object(self.flow, "releases_for", side_effect=blocking) if phase == "release" else patch.object(install_flow, "backup_data" if phase == "backup" else "inspect_archive", side_effect=blocking)
                with scope:
                    op = await self.flow.prepare("acme/example", "v2", URL, "Example")
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    pending = asyncio.create_task(self.flow.cancel(op["id"]))
                    await asyncio.sleep(0.02)
                    self.assertTrue(json.loads(self.flow.file.read_text())["cancel_requested"])
                    self.assertFalse(pending.done())
                    with self.assertRaises(RuntimeError):
                        await self.flow.prepare("acme/example", "v2", URL, "Example")
                    release.set()
                    result = await pending
                self.assertTrue(finished.is_set())
                self.assertEqual(result["stage"], "cancelled")
                before = self.flow.file.read_bytes()
                await asyncio.sleep(0.02)
                self.assertEqual(self.flow.file.read_bytes(), before)

    async def test_task52_cancel_validation_persists_without_waiting_long_lock_and_blocks_claim(self):
        op = await self.prepared_reinstall()
        started, release = threading.Event(), threading.Event()
        original = install_flow.file_sha256
        def blocking(path):
            started.set(); release.wait(2)
            return original(path)
        with patch.object(install_flow, "file_sha256", side_effect=blocking):
            claimed = asyncio.create_task(self.flow.claim_uninstall(op["id"]))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            pending = asyncio.create_task(self.flow.cancel(op["id"]))
            await asyncio.sleep(0.02)
            self.assertTrue(json.loads(self.flow.file.read_text())["cancel_requested"])
            self.assertFalse(pending.done())
            release.set()
            with self.assertRaises(RuntimeError):
                await claimed
            self.assertEqual((await pending)["stage"], "cancelled")
        with self.assertRaises(RuntimeError):
            await self.flow.claim_uninstall(op["id"])

    async def test_task52_cancel_real_stream_reclaims_writer_and_ignores_late_progress(self):
        started, finished = threading.Event(), threading.Event()
        def download(*args, **kwargs):
            try:
                download_asset(*args, **kwargs)
            finally:
                finished.set()
        prefix = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;extension="
        with trickling_response(prefix, ready=started), patch.object(install_flow, "download_asset", side_effect=download):
            op = await self.flow.prepare("acme/example", "v2", URL, None)
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            result = await asyncio.wait_for(self.flow.cancel(op["id"]), 0.5)
            self.assertTrue(finished.is_set())
            self.assertTrue(self.flow.task.done())
            self.assertEqual(result["stage"], "cancelled")
            before = self.flow.file.read_bytes()
            self.flow._download_progress(op["id"], threading.Event(), {"received": 999})
            await asyncio.sleep(0.04)
            self.assertEqual(self.flow.file.read_bytes(), before)

    async def test_task52_cancel_uninstall_started_stops_install_but_waits_absence_or_unknown(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        result = await self.flow.cancel(op["id"])
        self.assertEqual(result["stage"], "uninstalling")
        self.assertTrue(result["cancel_requested"])
        with self.assertRaises(RuntimeError):
            await self.flow.submit(op["id"], after_uninstall=True)
        self.assertEqual(self.flow.operation["stage"], "uninstalling")
        self.assertEqual((await self.flow.complete_cancelled_uninstall(op["id"]))["stage"], "unknown")
        with self.assertRaises(RuntimeError):
            await self.flow.close(op["id"])
        shutil.rmtree(self.home / "plugins" / "example")
        result = await self.flow.complete_cancelled_uninstall(op["id"])
        self.assertEqual(result["stage"], "cancelled")
        self.assertTrue(Path(result["backup"]["archive"]).is_file())
        self.assertTrue((self.home / "settings" / "example" / "config.json").is_file())

    async def assert_cancelled_uninstall_recheck_drained(self, path):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        await self.flow.cancel(op["id"])
        if path == "status":
            self.assertEqual((await self.flow.complete_cancelled_uninstall(op["id"]))["stage"], "unknown")
        shutil.rmtree(self.home / "plugins" / "example")
        started, release, hashed = (threading.Event() for _ in range(3))
        checked, commit = asyncio.Event(), asyncio.Event()
        original_hash = install_flow.file_sha256
        original_check = self.flow._check_prepared
        def blocked_hash(file):
            started.set()
            if not release.wait(2):
                raise AssertionError("test did not release hash worker")
            try:
                return original_hash(file)
            finally:
                hashed.set()
        async def checked_before_commit(*args, **kwargs):
            await original_check(*args, **kwargs)
            checked.set()
            await commit.wait()
        with patch.object(install_flow, "file_sha256", side_effect=blocked_hash), patch.object(self.flow, "_check_prepared", side_effect=checked_before_commit):
            rechecking = asyncio.create_task(self.flow.status() if path == "status" else self.flow.complete_cancelled_uninstall(op["id"]))
            unloading = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                unloading = asyncio.create_task(self.flow.unload())
                await asyncio.sleep(0.02)
                self.assertTrue(self.flow.stopping)
                self.assertFalse(hashed.is_set())
                self.assertFalse(unloading.done(), "unload returned while cancelled uninstall hash was running")
                release.set()
                await asyncio.wait_for(checked.wait(), 1)
                self.assertTrue(hashed.is_set())
                self.assertNotEqual(json.loads(self.flow.file.read_text())["stage"], "cancelled")
                self.assertFalse(unloading.done(), "unload drained only the thread, not the recheck persistence")
                commit.set()
                result = await rechecking
                await unloading
                self.assertEqual(result["stage"], "cancelled")
                self.assertEqual(json.loads(self.flow.file.read_text())["stage"], "cancelled")
                self.assertIsNone(self.flow.validation)
            finally:
                release.set()
                commit.set()
                await asyncio.gather(rechecking, *([unloading] if unloading else []), return_exceptions=True)
        reloaded = self.new_flow()
        self.addAsyncCleanup(reloaded.unload)
        self.assertEqual((await reloaded.status())["stage"], "cancelled")
        new = await reloaded.prepare("acme/example", "v2", URL, None)
        await reloaded.task
        self.assertEqual((await reloaded.status())["stage"], "ready")
        self.assertNotEqual(new["id"], op["id"])
        before = reloaded.file.read_bytes()
        with patch.object(install_flow, "file_sha256") as hash_worker:
            await self.flow.status()
            with self.assertRaisesRegex(RuntimeError, "插件正在卸载"):
                await self.flow.complete_cancelled_uninstall(op["id"])
        hash_worker.assert_not_called()
        await asyncio.sleep(0.02)
        self.assertEqual(reloaded.file.read_bytes(), before)

    async def test_task52_unload_drains_complete_cancelled_uninstall_through_persistence_before_reload(self):
        await self.assert_cancelled_uninstall_recheck_drained("complete_cancelled_uninstall")

    async def test_task52_unload_drains_status_cancelled_uninstall_through_persistence_before_reload(self):
        await self.assert_cancelled_uninstall_recheck_drained("status")

    async def test_task52_stopped_flow_cannot_start_cancelled_uninstall_rechecks(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        await self.flow.cancel(op["id"])
        self.assertEqual((await self.flow.complete_cancelled_uninstall(op["id"]))["stage"], "unknown")
        await self.flow.unload()
        before = self.flow.file.read_bytes()
        with patch.object(install_flow, "file_sha256") as hash_worker:
            self.assertEqual((await self.flow.status())["stage"], "unknown")
            with self.assertRaisesRegex(RuntimeError, "插件正在卸载"):
                await self.flow.complete_cancelled_uninstall(op["id"])
        hash_worker.assert_not_called()
        self.assertEqual(self.flow.file.read_bytes(), before)

    async def test_task52_cancel_claim_not_started_and_reload_never_authorize_native(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        result = await self.flow.cancel(op["id"])
        self.assertEqual(result["stage"], "cancelled")
        await self.flow.event(op["id"], "uninstall_started", "Example")
        self.assertFalse(self.flow.operation.get("uninstall_started"))
        reloaded = self.new_flow()
        with self.assertRaises(RuntimeError):
            await reloaded.submit(op["id"], after_uninstall=True)

    async def test_task52_pending_cancel_unknown_keeps_conflict_and_started_install_still_finalizes(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        with self.assertRaises(RuntimeError):
            await self.flow.cancel(op["id"])
        await self.flow.event(op["id"], "prompt", "Example", "req", op["package"]["sha256"])
        result = await self.flow.cancel(op["id"])
        self.assertEqual(result["stage"], "awaiting")
        await self.flow.event(op["id"], "cancel_unknown", "Example")
        self.assertEqual(self.flow.operation["stage"], "unknown")
        with self.assertRaises(RuntimeError):
            await self.flow.close(op["id"])
        with self.assertRaises(RuntimeError):
            await self.flow.prepare("acme/example", "v2", URL, None)
        await self.flow.event(op["id"], "start", "Example")
        with self.assertRaises(RuntimeError):
            await self.flow.cancel(op["id"])
        await self.flow.event(op["id"], "cancelled", "Example")
        self.assertEqual(self.flow.operation["stage"], "installing")
        with ZipFile(self.source) as archive:
            archive.extractall(self.home / "plugins")
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual((await self.flow.status(Mock()))["outcome"], "success")


    async def test_task52_late_cancelled_uninstall_complete_rejects_install_evidence_without_mutation(self):
        import main
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        shutil.rmtree(self.home / "plugins" / "example")
        await self.flow.submit(op["id"], after_uninstall=True)
        await self.flow.event(op["id"], "prompt", "Example", "known", op["package"]["sha256"])
        await self.flow.cancel(op["id"])
        await self.flow.event(op["id"], "cancel_unknown", "Example")
        plugin = main.Plugin(); plugin._flow = self.flow
        initial = dict(self.flow.operation)
        evidence_cases = [
            {"stage": "unknown", "submitted_at": initial["submitted_at"]},
            {"stage": "unknown", "request_id": "known"},
            {"stage": "unknown", "saw_start": True},
            {"stage": "unknown", "saw_finish": True},
            {"stage": "awaiting"},
            {"stage": "installing", "saw_start": True},
            {"stage": "verified", "saw_start": True, "saw_finish": True},
            {"stage": "closed", "saw_start": True, "saw_finish": True, "outcome": "success"},
            {"stage": "cancelled", "request_id": "known"},
        ]
        for evidence in evidence_cases:
            with self.subTest(evidence=evidence):
                self.flow.operation = {k: v for k, v in initial.items() if k not in ("submitted_at", "request_id", "saw_start", "saw_finish")}
                self.flow._update(**evidence)
                before = self.flow.file.read_bytes()
                with patch.object(self.flow, "_check_prepared") as recheck:
                    with self.assertRaisesRegex(RuntimeError, "没有可回查"):
                        await plugin.complete_uninstall(op["id"])
                recheck.assert_not_called()
                self.assertEqual(self.flow.file.read_bytes(), before)
                self.assertEqual(self.flow.operation, json.loads(before))
        self.flow.operation = initial
        self.flow._update()
        for phase in ("unknown", "start", "finish", "files"):
            with self.subTest(phase=phase):
                if phase in ("start", "finish"):
                    await self.flow.event(op["id"], phase, "Example")
                if phase == "files":
                    with ZipFile(self.source) as archive:
                        archive.extractall(self.home / "plugins")
                before = self.flow.file.read_bytes()
                with patch.object(self.flow, "_check_prepared") as recheck:
                    with self.assertRaisesRegex(RuntimeError, "没有可回查"):
                        await plugin.complete_uninstall(op["id"])
                recheck.assert_not_called()
                self.assertEqual(self.flow.file.read_bytes(), before)
                if phase == "unknown":
                    with self.assertRaises(RuntimeError):
                        await self.flow.close(op["id"])
                    with self.assertRaises(RuntimeError):
                        await self.flow.prepare("acme/example", "v2", URL, None)
        finalize = Mock()
        result = await self.flow.status(finalize)
        self.assertEqual(result["stage"], "closed")
        self.assertEqual(result["outcome"], "success")
        self.assertTrue(result["binding_saved"])
        self.assertTrue(result["saw_start"])
        self.assertTrue(result["saw_finish"])
        self.assertEqual(result["request_id"], "known")
        finalize.assert_called_once()
        self.assertTrue(Path(result["backup"]["archive"]).is_file())
        self.assertTrue((self.home / "settings" / "example" / "config.json").is_file())

    async def test_task52_status_never_uses_uninstall_absence_for_submitted_cancelled_install(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        shutil.rmtree(self.home / "plugins" / "example")
        await self.flow.submit(op["id"], after_uninstall=True)
        await self.flow.event(op["id"], "prompt", "Example", "known", op["package"]["sha256"])
        await self.flow.cancel(op["id"])
        await self.flow.event(op["id"], "cancel_unknown", "Example")
        self.flow._update(uninstall_result_received=True)
        before = self.flow.file.read_bytes()
        with patch.object(self.flow, "_check_prepared") as recheck:
            result = await self.flow.status()
        recheck.assert_not_called()
        self.assertEqual(result["stage"], "unknown")
        self.assertEqual(self.flow.file.read_bytes(), before)
        with self.assertRaises(RuntimeError):
            await self.flow.prepare("acme/example", "v2", URL, None)
        await self.flow.event(op["id"], "start", "Example")
        with ZipFile(self.source) as archive:
            archive.extractall(self.home / "plugins")
        await self.flow.event(op["id"], "finish", "Example")
        self.assertEqual((await self.flow.status(Mock()))["outcome"], "success")

    async def test_task52_cancel_submit_validation_never_authorizes_native_and_waiters_hold_new_task(self):
        op = await self.prepared()
        started, release = threading.Event(), threading.Event()
        original = install_flow.file_sha256
        def blocked(path):
            started.set(); release.wait(2)
            return original(path)
        with patch.object(install_flow, "file_sha256", side_effect=blocked):
            submitting = asyncio.create_task(self.flow.submit(op["id"]))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            cancelling = asyncio.create_task(self.flow.cancel(op["id"]))
            duplicate = asyncio.create_task(self.flow.cancel(op["id"]))
            await asyncio.sleep(0.02)
            self.assertTrue(self.flow.operation["cancel_requested"])
            self.assertFalse(cancelling.done())
            release.set()
            with self.assertRaises(RuntimeError):
                await submitting
            first, second = await asyncio.gather(cancelling, duplicate)
        self.assertEqual(first["id"], op["id"])
        self.assertEqual(second["stage"], "cancelled")
        self.assertNotIn("submitted_at", self.flow.operation)

    async def test_task52_old_ready_cancel_flag_and_pending_cancel_reload_do_not_replay(self):
        op = await self.prepared()
        self.flow._update(cancel_requested=True)
        reloaded = self.new_flow()
        self.assertEqual(reloaded.operation["stage"], "cancelled")
        with self.assertRaises(RuntimeError):
            await reloaded.submit(op["id"])
        self.flow._update(stage="awaiting", request_id="known", submitted_at=time.time())
        reloaded = self.new_flow()
        self.assertEqual(reloaded.operation["stage"], "unknown")
        with self.assertRaises(RuntimeError):
            await reloaded.close(op["id"])
        self.assertTrue(reloaded.operation["cancel_requested"])

    async def test_task52_main_cancel_does_not_require_changed_store_eligibility(self):
        import main
        op = await self.prepared_reinstall()
        plugin = main.Plugin(); plugin._flow = self.flow
        with patch.object(plugin, "_gate_operation", side_effect=RuntimeError("store changed")) as gate:
            result = await plugin.cancel_operation(op["id"])
        self.assertEqual(result["stage"], "cancelled")
        gate.assert_not_called()

    async def test_task52_cancel_after_native_dispatch_never_releases_on_error_or_reload(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        await self.flow.cancel(op["id"])
        await self.flow.event(op["id"], "uninstall_error", "Example", "timeout")
        shutil.rmtree(self.home / "plugins" / "example")
        result = await self.new_flow().status()
        self.assertEqual(result["stage"], "unknown")
        self.assertTrue(result["uninstall_blocked"])
        with self.assertRaises(RuntimeError):
            await self.flow.close(op["id"])

    async def test_task52_cancel_before_native_install_dispatch_records_exact_terminal_without_success(self):
        op = await self.prepared()
        await self.flow.submit(op["id"])
        result = await self.flow.event(op["id"], "dispatch_not_started", "Example")
        self.assertEqual(result["stage"], "cancelled")
        self.assertTrue(result["cancel_requested"])
        self.assertIsNone(result["outcome"])
        self.assertFalse(result["binding_saved"])
        await self.flow.event(op["id"], "prompt", "Example", "late", op["package"]["sha256"])
        self.assertIsNone(self.flow.operation["request_id"])


    async def test_task52_dismiss_notice_exact_terminal_persist_only_metadata_and_never_cancel(self):
        op = await self.prepared()
        with self.assertRaises(RuntimeError):
            await self.flow.dismiss(op["id"])
        self.flow._update(stage="failed", error="original error")
        before = dict(self.flow.operation)
        with self.assertRaises(RuntimeError):
            await self.flow.dismiss("stale")
        with patch.object(install_flow, "save_settings", side_effect=OSError("disk full")), self.assertRaises(OSError):
            await self.flow.dismiss(op["id"])
        self.assertEqual(self.flow.operation, before)
        dismissed = await self.flow.dismiss(op["id"])
        self.assertTrue(dismissed["notice_dismissed"])
        self.assertEqual({k: v for k, v in self.flow.operation.items() if k != "notice_dismissed"}, before)
        self.assertFalse(self.flow.download_cancel.is_set())
        self.assertEqual(await self.flow.dismiss(op["id"]), dismissed)
        self.assertTrue((await self.new_flow().status())["notice_dismissed"])
        new = await self.prepared()
        self.assertNotEqual(new["id"], op["id"])
        self.assertFalse(new.get("notice_dismissed", False))
        with self.assertRaises(RuntimeError):
            await self.flow.dismiss(op["id"])

    async def test_task52_dismiss_success_retains_all_evidence_backup_binding_and_outcome(self):
        await self.verified_operation()
        closed = await self.flow.status(Mock())
        before = dict(self.flow.operation)
        result = await self.flow.dismiss(closed["id"])
        self.assertEqual(result["outcome"], "success")
        self.assertTrue(result["binding_saved"])
        self.assertEqual({k: v for k, v in self.flow.operation.items() if k != "notice_dismissed"}, before)
        self.assertTrue(Path(result["directory"]).is_dir())


    async def test_task52_cancel_during_complete_validation_prevents_future_install_and_accounts_result(self):
        op = await self.prepared_reinstall()
        await self.flow.claim_uninstall(op["id"])
        await self.flow.event(op["id"], "uninstall_started", "Example")
        shutil.rmtree(self.home / "plugins" / "example")
        started, release = threading.Event(), threading.Event()
        original = install_flow.file_sha256
        def blocked(path):
            started.set(); release.wait(2)
            return original(path)
        with patch.object(install_flow, "file_sha256", side_effect=blocked):
            completing = asyncio.create_task(self.flow.submit(op["id"], after_uninstall=True))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            cancelling = asyncio.create_task(self.flow.cancel(op["id"]))
            await asyncio.sleep(0.02)
            self.assertTrue(json.loads(self.flow.file.read_text())["cancel_requested"])
            self.assertFalse(cancelling.done())
            release.set()
            with self.assertRaises(RuntimeError):
                await completing
            self.assertEqual((await cancelling)["stage"], "unknown")
        self.assertNotIn("submitted_at", self.flow.operation)
        result = await self.flow.complete_cancelled_uninstall(op["id"])
        self.assertEqual(result["stage"], "cancelled")
        self.assertTrue(result["uninstall_result_received"])
        self.assertTrue(Path(result["backup"]["archive"]).is_file())
