"""Store consent, durable intent, and mutation bypass regressions."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
import sideload
from sideload.ownership import Ownership, read_frozen
from sideload.persist import load_settings, save_settings


class OwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.folder = self.home / "plugins" / "one"
        self.folder.mkdir(parents=True)
        (self.folder / "plugin.json").write_text('{"name":"One"}')
        (self.folder / "package.json").write_text('{"version":"1","repository":"acme/One"}')
        self.loader = self.home / "settings" / "loader.json"
        self.loader.parent.mkdir()
        self.freeze([])
        patcher = patch.multiple(main.decky, create=True, DECKY_HOME=str(self.home), DECKY_PLUGIN_NAME="decky-sideloader",
                                 DECKY_PLUGIN_RUNTIME_DIR=str(self.home / "data" / "self"), DECKY_PLUGIN_SETTINGS_DIR=str(self.home / "settings" / "self"), logger=Mock())
        patcher.start(); self.addCleanup(patcher.stop)
        self.plugin = main.Plugin()
        self.names, self.store_ok = {"One"}, True
        def resolve():
            return self.names, {"source": "live" if self.store_ok else "unavailable", "stale": False, "warning": None, "timestamp": 1}, []
        self.plugin._catalog = Mock(resolve=Mock(side_effect=resolve))
        async def native(refresh=False):
            return ({"ok": True, "plugins": [{"name": name, "version": "1", "load_type": 1, "disabled": False} for name in self.names | {"One"}]}, list(self.names) if self.store_ok else None)
        self.plugin._read_native_management = native
        self.owner = self.plugin._ownership()
        settings = load_settings(sideload.paths._settings_path())
        settings["bindings"]["One"] = {"repo": "legacy/One", "overridden": True}
        save_settings(sideload.paths._settings_path(), settings)

    def freeze(self, names):
        self.loader.write_text(json.dumps({"frozenPlugins": names, "unrelated": {"private": "never returned"}}))

    async def row(self):
        return (await self.plugin.list_sideload_plugins())["plugins"][0]

    async def takeover(self):
        intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        await self.plugin.ownership_native_step(intent["id"], "writing")
        self.freeze(["One", "Other"])
        await self.plugin.ownership_native_step(intent["id"], "confirming")
        await self.plugin.ownership_native_step(intent["id"], "complete")
        return intent

    async def test_legacy_repo_metadata_and_reports_do_not_grant_store_consent(self):
        row = await self.row()
        self.assertEqual(row["repo"], "legacy/One")
        self.assertEqual(row["ownership"]["state"], "store")
        self.assertFalse(row["sideload"])
        with patch.object(self.plugin, "_release_reader", return_value=Mock()) as releases:
            for source in ("manual", "scheduled"):
                result = await self.plugin._check_updates(source)
                self.assertEqual(result["last_check"]["items"], [])
            releases.return_value.assert_not_called()
        for call in (self.plugin.set_binding("One", "other/repo"), self.plugin.clear_binding("One"), self.plugin.authorize_plugin_mutation("One"), self.plugin.prepare_install("acme/One", "v1", "url", "One")):
            with self.assertRaises(RuntimeError):
                await call
        self.assertEqual(load_settings(sideload.paths._settings_path())["bindings"]["One"]["repo"], "legacy/One")

    async def test_intent_precedes_effect_and_native_completion_is_not_activation(self):
        intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        self.assertEqual(self.owner.load()["entries"][0], intent)
        self.assertFalse(intent["prior_frozen"])
        self.assertEqual(read_frozen(str(self.home)), [])
        self.assertEqual((await self.row())["ownership"]["state"], "pending")
        with self.assertRaises(RuntimeError):
            await self.plugin.ownership_native_step(intent["id"], "complete")
        await self.plugin.ownership_native_step(intent["id"], "writing")
        self.freeze(["Other", "One"])
        await self.plugin.ownership_native_step(intent["id"], "confirming")
        self.assertFalse((await self.row())["sideload"])
        await self.plugin.ownership_native_step(intent["id"], "complete")
        self.assertTrue((await self.row())["sideload"])
        self.assertEqual((await self.row())["repo"], "acme/One")
        await self.plugin.authorize_plugin_mutation("One")
        self.assertNotIn("unrelated", await self.plugin.ownership_native_step(intent["id"], "read"))
        self.assertEqual(read_frozen(str(self.home)), ["Other", "One"])

    async def test_persistence_failure_does_not_publish_or_lose_old_ledger(self):
        with patch("sideload.ownership.save_settings", side_effect=OSError("disk full")), self.assertRaises(OSError):
            await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        self.assertEqual(self.owner.load()["entries"], [])
        await self.takeover()
        before = self.owner.path.read_bytes()
        with patch("sideload.ownership.save_settings", side_effect=OSError("disk full")), self.assertRaises(OSError):
            await self.plugin.begin_store_ownership("One", "one", "acme/One", "return")
        self.assertEqual(self.owner.path.read_bytes(), before)
        self.assertEqual(read_frozen(str(self.home)), ["One", "Other"])

    async def test_return_restores_only_prior_policy_and_keeps_legacy_binding(self):
        for prior in (False, True):
            if self.owner.path.exists():
                self.owner.path.unlink()
            self.freeze(["One", "Before"] if prior else ["Before"])
            old = await self.takeover()
            intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "return")
            self.assertEqual(intent["prior_frozen"], prior)
            with self.assertRaises(RuntimeError):
                await self.plugin.ownership_native_step(old["id"], "complete")
            await self.plugin.ownership_native_step(intent["id"], "writing")
            self.freeze(["Other", "Concurrent"] + (["One"] if prior else []))
            await self.plugin.ownership_native_step(intent["id"], "confirming")
            await self.plugin.ownership_native_step(intent["id"], "complete")
            row = await self.row()
            self.assertEqual(row["ownership"]["state"], "store")
            self.assertFalse(row["sideload"])
            self.assertTrue(self.folder.is_dir())
            self.assertEqual(load_settings(sideload.paths._settings_path())["bindings"]["One"]["repo"], "legacy/One")
            self.assertEqual(read_frozen(str(self.home)), ["Other", "Concurrent"] + (["One"] if prior else []))

    async def test_partial_failure_reload_and_explicit_readback_never_auto_retry(self):
        intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        await self.plugin.ownership_native_step(intent["id"], "writing")
        # Native failure or timeout leaves durable writing; reload is read-only.
        before = self.owner.path.read_bytes()
        self.assertFalse((await self.row())["sideload"])
        self.assertEqual(Ownership(self.owner.path).load()["entries"][0]["state"], "writing")
        self.assertEqual(self.owner.path.read_bytes(), before)
        with self.assertRaises(RuntimeError):
            await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        with self.assertRaises(RuntimeError):
            await self.plugin.ownership_native_step(intent["id"], "complete")
        self.freeze(["One"])
        # Recovery is an explicit correlated readback, not a native write replay.
        await self.plugin.ownership_native_step(intent["id"], "complete")
        self.assertTrue((await self.row())["sideload"])
        self.freeze([])
        self.assertFalse((await self.row())["sideload"])
        with self.assertRaises(RuntimeError):
            await self.plugin.ownership_native_step(intent["id"], "complete")

    async def test_unknown_native_capability_is_not_consent_even_with_explicit_binding(self):
        self.store_ok = False
        self.assertEqual((await self.row())["ownership"]["state"], "unknown")
        with self.assertRaises(RuntimeError):
            await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        save_settings(sideload.paths._settings_path(), load_settings(str(self.home / "absent-settings.json")))
        self.assertFalse((await self.row())["sideload"])
        await self.plugin.set_binding("One", "repair/One")
        self.assertFalse((await self.row())["sideload"])
        self.store_ok, self.names = True, set()
        self.assertEqual((await self.row())["ownership"]["state"], "nonstore")
        await self.plugin.clear_binding("One")
        self.assertIsNone((await self.row())["repo"])
        self.assertTrue((await self.row())["sideload"])

    async def test_duplicate_name_changed_folder_self_and_active_target_block_changes(self):
        for name, folder in (("missing", "one"), ("decky-sideloader", "one"), ("One", "wrong")):
            with self.assertRaises(RuntimeError):
                await self.plugin.begin_store_ownership(name, folder, "acme/One", "takeover")
        other = self.home / "plugins" / "duplicate"
        other.mkdir(); (other / "plugin.json").write_text('{"name":"One"}')
        with self.assertRaises(RuntimeError):
            await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        self.assertTrue(all(not p["sideload"] for p in (await self.plugin.list_sideload_plugins())["plugins"]))
        (other / "plugin.json").unlink(); other.rmdir()
        await self.takeover()
        for field in ("existing_name", "package", "target"):
            operation = {"stage": "ready", field: "One" if field == "existing_name" else {"name": "One"}}
            self.plugin._flow = types.SimpleNamespace(operation=operation)
            with self.assertRaises(RuntimeError):
                await self.plugin.begin_store_ownership("One", "one", "acme/One", "return")
        self.plugin._flow.operation = None
        self.folder.rename(self.folder.with_name("replacement"))
        self.assertFalse((await self.row())["sideload"])

    async def test_old_ready_submit_claim_and_complete_cannot_bypass_store_gate(self):
        flow = Mock(operation={"id": "old", "stage": "ready", "existing_name": "One", "package": {"name": "One", "folder": "one"}},
                    submit=AsyncMock(), claim_uninstall=AsyncMock())
        self.plugin._flow = flow
        for method in (self.plugin.submit_prepared, self.plugin.claim_uninstall, self.plugin.complete_uninstall):
            with self.assertRaises(RuntimeError):
                await method("old")
        flow.submit.assert_not_awaited(); flow.claim_uninstall.assert_not_awaited()
        # New-install repository matching cannot silently replace a store plugin.
        del self.plugin._flow
        with patch.object(self.plugin, "probe_repository", new=AsyncMock(return_value={"tree_sha": "a" * 40, "plugins": [{"name": "One", "path": ""}]})):
            with self.assertRaises(RuntimeError):
                await self.plugin.prepare_install("acme/One", "v1", "url", None, "")

    async def test_legal_native_uninstall_absence_preserves_owned_and_nonstore_flow(self):
        await self.takeover()
        operation = {"id": "job", "stage": "uninstalling", "mode": "reinstall", "repo": "acme/One", "existing_name": "One", "package": {"name": "One", "folder": "one"}}
        flow = Mock(operation=operation, submit=AsyncMock(return_value={"stage": "awaiting"}))
        self.plugin._flow = flow
        self.folder.rename(self.folder.with_name("hidden"))
        # Remove metadata from the hidden fixture to represent actual absence.
        (self.folder.with_name("hidden") / "plugin.json").unlink()
        await self.plugin.complete_uninstall("job")
        flow.submit.assert_awaited_once_with("job", after_uninstall=True)
        self.owner.path.unlink()
        self.names = set()
        flow.submit.reset_mock()
        await self.plugin.complete_uninstall("job")
        flow.submit.assert_awaited_once_with("job", after_uninstall=True)

    async def test_success_binding_replacement_cannot_erase_independent_ownership(self):
        await self.takeover()
        before = self.owner.path.read_bytes()
        async def status(finalize):
            operation = {"repo": "acme/new", "package": {"name": "One"}}
            finalize(operation)
            return operation
        self.plugin._flow = Mock(status=AsyncMock(side_effect=status))
        await self.plugin.get_operation()
        self.assertEqual(self.owner.path.read_bytes(), before)
        self.assertEqual(load_settings(sideload.paths._settings_path())["bindings"]["One"], {"repo": "acme/new", "overridden": True})
        self.assertEqual((await self.row())["repo"], "acme/One")

    async def test_check_finishing_after_return_returns_current_ineligible_row(self):
        await self.takeover()
        entered, release = asyncio.Event(), asyncio.Event()
        def releases(*args):
            # The test changes ownership on the event loop while GitHub runs.
            return [{"tag": "2", "has_zip": True, "comparison": 1}]
        async def delayed_to_thread(fn, *args, **kwargs):
            if fn is releases:
                entered.set(); await release.wait()
            return fn(*args, **kwargs)
        with patch.object(self.plugin, "_release_reader", return_value=releases), patch.object(main.asyncio, "to_thread", side_effect=delayed_to_thread):
            checking = asyncio.create_task(self.plugin.check_updates())
            await entered.wait()
            intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "return")
            await self.plugin.ownership_native_step(intent["id"], "writing")
            self.freeze(["Other"])
            await self.plugin.ownership_native_step(intent["id"], "complete")
            release.set()
            listing = await checking
        self.assertEqual(listing["last_check"]["items"][0]["status"], "update")
        self.assertFalse(listing["plugins"][0]["ownership"]["eligible"])

    async def test_malformed_ledger_and_native_settings_never_default_to_success(self):
        await self.takeover()
        valid = self.owner.load()
        for invalid in ({}, {"version": True, "entries": []}, {"version": 1, "entries": [valid["entries"][0]] * 2}, {"version": 1, "entries": [{**valid["entries"][0], "prior_frozen": 1}]}):
            self.owner.path.write_text(json.dumps(invalid))
            self.assertFalse((await self.row())["sideload"])
            with self.assertRaises(RuntimeError):
                self.owner.load()
        self.owner.path.write_text(json.dumps(valid))
        for invalid in ("bad json", '[]', '{"frozenPlugins":null}', '{"frozenPlugins":["One","One"]}', '{"frozenPlugins":[3]}'):
            self.loader.write_text(invalid)
            with self.assertRaises(RuntimeError):
                read_frozen(str(self.home))
            self.assertFalse((await self.row())["sideload"])
        self.loader.unlink(); self.loader.symlink_to(self.owner.path)
        with self.assertRaises(RuntimeError):
            read_frozen(str(self.home))
        self.loader.unlink(); self.loader.write_bytes(b" " * (4 * 1024 * 1024 + 1))
        with self.assertRaises(RuntimeError):
            read_frozen(str(self.home))
        self.owner.path.unlink(); self.owner.path.symlink_to(self.loader)
        with self.assertRaises(RuntimeError):
            self.owner.load()

    async def test_rejected_oversized_repo_keeps_existing_ledger_readable_and_returnable(self):
        await self.takeover()
        second = self.home / "plugins" / "two"
        second.mkdir()
        (second / "plugin.json").write_text('{"name":"Two"}')
        (second / "package.json").write_text('{"version":"1"}')
        self.names.add("Two")
        before = self.owner.path.read_bytes()
        with self.assertRaises(RuntimeError):
            await self.plugin.begin_store_ownership("Two", "two", "a/" + "b" * 255, "takeover")
        self.assertEqual(self.owner.path.read_bytes(), before)
        self.assertEqual(len(self.owner.load()["entries"]), 1)
        self.assertTrue(next(p for p in (await self.plugin.list_sideload_plugins())["plugins"] if p["name"] == "One")["sideload"])
        intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "return")
        await self.plugin.ownership_native_step(intent["id"], "writing")
        self.freeze(["Other"])
        await self.plugin.ownership_native_step(intent["id"], "complete")
        self.assertEqual(self.owner.load()["entries"][0]["state"], "released")

    async def test_save_rejects_invalid_complete_candidate_without_replacing_ledger(self):
        await self.takeover()
        before = self.owner.path.read_bytes()
        for field, bad in (("name", "bad\nname"), ("name", "n" * 257), ("folder", "../one"), ("folder", ".."), ("repo", "a/" + "b" * 255)):
            candidate = self.owner.load()
            candidate["entries"][0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(RuntimeError):
                self.owner.save(candidate)
            self.assertEqual(self.owner.path.read_bytes(), before)
            self.assertEqual(self.owner.load()["entries"][0]["state"], "active")

    async def test_save_checks_the_exact_utf8_serialized_byte_limit_before_replacement(self):
        await self.takeover()
        before = self.owner.path.read_bytes()
        candidate = self.owner.load()
        candidate["entries"][0]["name"] = "n" * 40
        limit = len(before) + 1
        self.assertLess(len(json.dumps(candidate).encode()), limit)
        self.assertGreater(len(json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8")), limit)
        with patch("sideload.ownership.MAX_BYTES", limit), self.assertRaisesRegex(RuntimeError, "容量"):
            self.owner.save(candidate)
        self.assertEqual(self.owner.path.read_bytes(), before)
        self.assertEqual(self.owner.load()["entries"][0]["name"], "One")

    async def test_owned_repo_rejects_other_prepare_and_old_operations_without_mutating_evidence(self):
        await self.takeover()
        ledger_before = self.owner.path.read_bytes()
        settings_before = Path(sideload.paths._settings_path()).read_bytes()
        operation = {"id": "job", "stage": "ready", "mode": "reinstall", "repo": "other/One", "existing_name": "One",
                     "package": {"name": "One", "folder": "one"}, "saw_finish": False, "outcome": None}
        flow = Mock(operation=operation, prepare=AsyncMock(), submit=AsyncMock(), claim_uninstall=AsyncMock())
        self.plugin._flow = flow
        for mode in ("install", "reinstall"):
            with self.assertRaisesRegex(RuntimeError, "仓库"):
                await self.plugin.prepare_install("https://github.com/other/One", "v1", "url", "One", mode=mode)
        flow.prepare.assert_not_awaited()
        ready_snapshot = json.loads(json.dumps(operation))
        for method in (self.plugin.submit_prepared, self.plugin.claim_uninstall):
            with self.assertRaisesRegex(RuntimeError, "仓库"):
                await method("job")
            self.assertEqual(operation, ready_snapshot)
        operation["stage"] = "uninstalling"
        for path in self.folder.iterdir():
            path.unlink()
        self.folder.rmdir()
        snapshot = json.loads(json.dumps(operation))
        with self.assertRaisesRegex(RuntimeError, "仓库"):
            await self.plugin.complete_uninstall("job")
        flow.submit.assert_not_awaited()
        flow.claim_uninstall.assert_not_awaited()
        self.assertEqual(operation, snapshot)
        self.assertEqual(self.owner.path.read_bytes(), ledger_before)
        self.assertEqual(Path(sideload.paths._settings_path()).read_bytes(), settings_before)

    async def test_owned_confirmed_repo_allows_normal_update_and_reinstall(self):
        await self.takeover()
        operation = {"id": "job", "stage": "ready", "mode": "reinstall", "repo": "https://github.com/ACME/one", "existing_name": "One",
                     "package": {"name": "One", "folder": "one"}}
        flow = Mock(operation=operation, prepare=AsyncMock(return_value={"stage": "checking"}),
                    submit=AsyncMock(return_value={"stage": "awaiting"}), claim_uninstall=AsyncMock(return_value={"stage": "uninstalling"}))
        self.plugin._flow = flow
        await self.plugin.prepare_install("https://github.com/ACME/one", "v1", "url", "One")
        flow.prepare.assert_awaited_once_with("ACME/one", "v1", "url", "One", None, mode="install")
        await self.plugin.prepare_install("acme/One", "v1", "url", "One", mode="reinstall")
        self.assertEqual(flow.prepare.await_count, 2)
        flow.prepare.assert_awaited_with("acme/One", "v1", "url", "One", None, mode="reinstall")
        await self.plugin.submit_prepared("job")
        flow.submit.assert_awaited_once_with("job")
        await self.plugin.claim_uninstall("job")
        flow.claim_uninstall.assert_awaited_once_with("job")
        operation["stage"] = "uninstalling"
        for path in self.folder.iterdir():
            path.unlink()
        self.folder.rmdir()
        await self.plugin.complete_uninstall("job")
        self.assertEqual(flow.submit.await_count, 2)
        flow.submit.assert_awaited_with("job", after_uninstall=True)

    async def test_activation_save_failure_and_missing_native_file_remain_unconfirmed(self):
        intent = await self.plugin.begin_store_ownership("One", "one", "acme/One", "takeover")
        await self.plugin.ownership_native_step(intent["id"], "writing")
        self.freeze(["One"])
        await self.plugin.ownership_native_step(intent["id"], "confirming")
        before = self.owner.path.read_bytes()
        with patch("sideload.ownership.save_settings", side_effect=OSError("activation full")), self.assertRaises(OSError):
            await self.plugin.ownership_native_step(intent["id"], "complete")
        self.assertEqual(self.owner.path.read_bytes(), before)
        self.assertFalse((await self.row())["sideload"])
        self.loader.unlink()
        with self.assertRaises(RuntimeError):
            await self.plugin.ownership_native_step(intent["id"], "complete")
        self.assertFalse((await self.row())["sideload"])
        self.loader.write_text("{}")
        self.assertEqual(read_frozen(str(self.home)), [])

    async def test_recovered_ready_target_blocks_return_without_finalizing_or_replaying(self):
        await self.takeover()
        runtime = self.home / "runtime"
        operation = {"id": "old", "stage": "ready", "existing_name": "One", "package": {"name": "One", "folder": "one"}}
        file = runtime / "installations" / "operation.json"
        save_settings(str(file), operation)
        with patch.multiple(main.decky, create=True, DECKY_PLUGIN_RUNTIME_DIR=str(runtime), DECKY_PLUGIN_DIR=str(self.home / "plugins" / "self")):
            with self.assertRaisesRegex(RuntimeError, "安装任务"):
                await self.plugin.begin_store_ownership("One", "one", "acme/One", "return")
        self.assertEqual(json.loads(file.read_text()), operation)
        self.assertEqual(self.plugin._flow.operation, operation)
        self.assertTrue((await self.row())["sideload"])
