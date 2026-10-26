"""Local relationships survive restarts without implicit catalog requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import sideload
from sideload.catalog import Catalog


class LocalCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'catalog.json'
        self.now = 1000
        self.fetch = Mock(return_value=({'Store'}, None)); self.registry = Mock(side_effect=RuntimeError('offline'))
        self.plugin = {'name':'Local','folder':'local','package_name':'local-package','repo':'acme/local'}
        self.catalog = self.reload()

    def reload(self):
        return Catalog(self.path, self.fetch, self.registry, lambda:self.now)

    def test_default_reads_do_not_request_store_or_registry(self):
        self.catalog.resolve(); self.now += 900000; self.reload().resolve()
        self.fetch.assert_not_called(); self.registry.assert_not_called()

    def test_explicit_refresh_bypasses_fresh_snapshot_and_read_does_not(self):
        self.catalog.resolve(refresh=True, plugins=[self.plugin]); self.catalog.resolve()
        self.catalog.resolve(refresh=True, plugins=[self.plugin])
        self.assertEqual(self.fetch.call_count,2); self.registry.assert_not_called()

    def test_offline_restart_beyond_seven_days_preserves_exact_negative_relationship(self):
        self.catalog.resolve(refresh=True, plugins=[self.plugin]); before=self.path.read_bytes()
        self.now += 30*86400; self.fetch.return_value=(set(),'offline'); fresh=self.reload()
        self.assertEqual(fresh.resolve()[0],{'Store'}); memory=fresh.memory_for(self.plugin)
        self.assertIs(memory['in_store'],False); self.assertEqual(memory['repo'],'acme/local'); self.assertFalse(memory['changed'])
        self.assertEqual(self.path.read_bytes(),before); self.assertEqual(self.fetch.call_count,1)

    def test_old_snapshot_absence_is_not_nonstore_proof_for_new_identity(self):
        self.catalog.resolve(refresh=True, plugins=[])
        self.assertIsNone(self.reload().memory_for(self.plugin)['in_store'])
        self.assertIs(self.reload().memory_for({'name':'Store','folder':'store','package_name':'store','repo':None})['in_store'],True)

    def test_folder_package_or_repo_change_does_not_inherit_old_permission(self):
        self.catalog.resolve(refresh=True, plugins=[self.plugin])
        for field,value in [('folder','replacement'),('package_name','different'),('repo','other/repo')]:
            with self.subTest(field=field):
                memory=self.reload().memory_for({**self.plugin,field:value})
                self.assertTrue(memory['changed']); self.assertIsNone(memory['in_store'])
        memory=self.reload().memory_for({**self.plugin,'version':'2'})
        self.assertFalse(memory['changed']); self.assertIs(memory['in_store'],False)

    def test_refresh_failure_or_save_failure_keeps_old_disk_and_memory(self):
        self.catalog.resolve(refresh=True,plugins=[self.plugin]); before=self.path.read_bytes()
        self.fetch.return_value=(set(),'offline'); self.catalog.resolve(refresh=True,plugins=[self.plugin])
        self.assertEqual(self.path.read_bytes(),before)
        self.fetch.return_value=({'Local'},None)
        with patch('sideload.catalog.save_settings', side_effect=OSError('disk full')):
            _,status,_=self.catalog.resolve(refresh=True,plugins=[self.plugin])
        self.assertEqual(self.path.read_bytes(),before); self.assertIs(self.catalog.memory_for(self.plugin)['in_store'],False)
        self.assertTrue(status['warning'])

    def test_explicit_binding_confirmation_updates_identity_without_inventing_negative_membership(self):
        self.catalog.resolve(refresh=True,plugins=[self.plugin]); changed={**self.plugin,'folder':'replacement'}
        self.catalog.remember(changed,'acme/local','user')
        memory=self.reload().memory_for(changed)
        self.assertFalse(memory['changed']); self.assertIsNone(memory['in_store']); self.assertEqual(memory['repo'],'acme/local')

    def test_symlink_hardlink_corrupt_and_oversized_memory_fail_closed_without_overwrite(self):
        self.catalog.resolve(refresh=True,plugins=[self.plugin]); before=self.path.read_bytes()
        foreign=self.path.with_name('foreign.json'); foreign.write_bytes(before)
        self.path.unlink(); self.path.symlink_to(foreign)
        loaded=self.reload(); self.assertTrue(loaded.memory_error)
        self.assertIsNone(loaded.memory_for(self.plugin)['in_store']); loaded.resolve(refresh=True,plugins=[self.plugin]); self.assertEqual(foreign.read_bytes(),before)
        self.path.unlink(); self.path.hardlink_to(foreign); self.assertTrue(self.reload().memory_error)
        self.path.unlink(); self.path.write_text('{bad'); self.assertTrue(self.reload().memory_error)
        self.path.write_bytes(before)
        with patch('sideload.catalog.MAX_CATALOG_BYTES', 1):self.assertTrue(self.reload().memory_error)


class LocalManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import types
        import asyncio
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        sys.modules.setdefault("decky", types.ModuleType("decky"))
        import main
        from sideload.persist import save_settings
        self.main = main
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name); self.folder = self.home / "plugins" / "one"; self.folder.mkdir(parents=True)
        (self.folder / "plugin.json").write_text('{"name":"One"}')
        (self.folder / "package.json").write_text('{"name":"one-package","version":"1","repository":"acme/one"}')
        settings_dir = self.home / "settings" / "self"; settings_dir.mkdir(parents=True)
        (self.home / "settings" / "loader.json").write_text('{"frozenPlugins":[]}')
        patcher = patch.multiple(main.decky, create=True, DECKY_HOME=str(self.home), DECKY_PLUGIN_NAME="decky-sideloader", DECKY_PLUGIN_RUNTIME_DIR=str(self.home / "data" / "self"), DECKY_PLUGIN_SETTINGS_DIR=str(settings_dir), logger=Mock())
        patcher.start(); self.addCleanup(patcher.stop)
        save_settings(sideload.paths._settings_path(), {"bindings":{}})
        self.fetch = Mock(return_value=({"Other"}, None)); self.clock=1000
        self.plugin = main.Plugin(); self.plugin._catalog = Catalog(settings_dir / "catalog.json", self.fetch, Mock(), lambda:self.clock)
        self.native_names = []
        async def native(refresh=False):
            return ({"ok": True, "plugins": [{"name": "One", "version": "1", "load_type": 1, "disabled": False}]}, self.native_names)
        self.plugin._read_native_management = native

    async def row(self):
        return (await self.plugin.list_sideload_plugins())["plugins"][0]

    async def test_default_list_check_and_authorization_never_fetch_store(self):
        await self.row(); self.fetch.assert_not_called()
        await self.plugin.list_sideload_plugins(True); self.fetch.assert_called_once()
        self.clock += 30*86400
        self.assertTrue((await self.row())["sideload"])
        await self.plugin.authorize_plugin_mutation("One")
        with patch.object(self.plugin, "_release_reader", return_value=Mock(return_value=[])):
            await self.plugin.check_updates()
        self.fetch.assert_called_once()

    async def test_known_store_and_legacy_binding_survive_removed_listing_and_restart_without_consent(self):
        from sideload.persist import save_settings
        self.fetch.return_value=({"One"},None)
        self.native_names = ["One"]
        await self.plugin.list_sideload_plugins(True)
        save_settings(sideload.paths._settings_path(), {"bindings":{"One":{"repo":"old/one","overridden":True}}})
        self.fetch.return_value=({"Other"},None)
        await self.plugin.list_sideload_plugins(True)
        self.clock += 30*86400
        self.plugin._catalog = Catalog(self.plugin._catalog.path,self.fetch,Mock(),lambda:self.clock)
        row=await self.row(); self.assertEqual(row["ownership"]["state"],"store"); self.assertFalse(row["sideload"])
        with self.assertRaises(RuntimeError):await self.plugin.authorize_plugin_mutation("One")
        intent=await self.plugin.begin_store_ownership("One","one","acme/one","takeover")
        self.assertEqual(intent["state"],"prepared"); self.assertEqual(json.loads((self.home/"settings"/"loader.json").read_text())["frozenPlugins"],[])
        self.assertFalse((await self.row())["sideload"])

    async def test_replacement_identity_requires_explicit_binding_confirmation(self):
        from sideload.persist import save_settings
        await self.plugin.list_sideload_plugins(True)
        save_settings(sideload.paths._settings_path(), {"bindings":{"One":{"repo":"acme/one","overridden":True}}})
        self.folder.rename(self.folder.with_name("replacement"))
        self.assertFalse((await self.row())["sideload"])
        await self.plugin.set_binding("One","acme/one")
        row=await self.row(); self.assertFalse(row["memory_changed"]); self.assertTrue(row["sideload"])
        (self.folder.with_name("replacement")/"package.json").write_text('{"name":"different-package","version":"1","repository":"acme/one"}')
        self.assertFalse((await self.row())["sideload"])

    async def test_missing_repository_metadata_is_remembered_but_unlink_remains_authoritative(self):
        await self.plugin.list_sideload_plugins(True)
        (self.folder/"package.json").write_text('{"name":"one-package","version":"2"}')
        row=await self.row(); self.assertEqual(row["repo"],"acme/one"); self.assertEqual(row["repo_source"],"remembered"); self.assertTrue(row["sideload"])
        await self.plugin.clear_binding("One")
        row=await self.row(); self.assertIsNone(row["repo"]); self.assertEqual(row["repo_source"],"disabled")

    async def test_active_task_or_failed_refresh_preserves_current_catalog(self):
        from types import SimpleNamespace
        await self.plugin.list_sideload_plugins(True); before=self.plugin._catalog.path.read_bytes()
        self.plugin._flow=SimpleNamespace(operation={"stage":"checking","existing_name":"One"})
        with self.assertRaises(RuntimeError):await self.plugin.list_sideload_plugins(True)
        self.fetch.assert_called_once(); self.plugin._flow.operation=None
        self.fetch.return_value=(set(),"offline")
        with self.assertRaisesRegex(RuntimeError,"刷新失败"):await self.plugin.list_sideload_plugins(True)
        self.assertEqual(self.plugin._catalog.path.read_bytes(),before)
        self.assertTrue((await self.row())["sideload"])


    async def test_unload_waits_for_real_catalog_worker_and_stopped_instance_cannot_refresh(self):
        import asyncio
        import threading
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set(); release.wait(2); return {"Other"}, None
        self.fetch.side_effect=fetch
        refreshing=asyncio.create_task(self.plugin.list_sideload_plugins(True))
        self.assertTrue(await asyncio.to_thread(entered.wait,1))
        closing=asyncio.create_task(self.plugin._unload())
        try:
            await asyncio.sleep(0)
            self.assertFalse(closing.done(),"unload must drain the actual catalog write worker")
        finally:
            release.set(); await refreshing; await closing
        before=self.plugin._catalog.path.read_bytes()
        with self.assertRaises(RuntimeError):await self.plugin.list_sideload_plugins(True)
        self.assertEqual(self.plugin._catalog.path.read_bytes(),before); self.fetch.assert_called_once()


    async def test_refresh_queued_before_stop_cannot_start_a_late_write(self):
        import asyncio
        self.plugin._catalog_lock=asyncio.Lock()
        await self.plugin._catalog_lock.acquire()
        refreshing=asyncio.create_task(self.plugin.list_sideload_plugins(True))
        await asyncio.sleep(0)
        await self.plugin._unload()
        self.plugin._catalog_lock.release()
        with self.assertRaises(RuntimeError):await refreshing
        self.fetch.assert_not_called(); self.assertFalse(self.plugin._catalog.path.exists())

    async def test_cancelled_rpc_still_drains_catalog_worker_before_unload_returns(self):
        import asyncio
        import threading
        entered, release=threading.Event(),threading.Event()
        def fetch():entered.set(); release.wait(2); return {"Other"},None
        self.fetch.side_effect=fetch
        refreshing=asyncio.create_task(self.plugin.list_sideload_plugins(True))
        self.assertTrue(await asyncio.to_thread(entered.wait,1))
        refreshing.cancel(); closing=asyncio.create_task(self.plugin._unload())
        try:
            await asyncio.sleep(0); self.assertFalse(closing.done())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):await refreshing
            await closing
        self.assertTrue(self.plugin._catalog.path.exists()); self.assertIsNone(self.plugin._catalog_worker)

    async def test_malformed_binding_identity_is_visible_but_does_not_grant_permission(self):
        from sideload.persist import save_settings
        save_settings(sideload.paths._settings_path(),{"bindings":{"One":{"repo":"acme/one","folder":"one","package_name":"one-package","package_repo":[]}}})
        row=await self.row(); self.assertTrue(row["memory_changed"]); self.assertFalse(row["sideload"])
