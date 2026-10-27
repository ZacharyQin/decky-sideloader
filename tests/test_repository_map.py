import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "backend"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
from sideload.persist import load_settings, save_settings
from sideload.repository_map import MAX_MAP_BYTES, RepositoryMap
from sideload.scan import read_plugins_dir


def document(revision=7, repositories=None):
    return {"schema_version": 1, "revision": revision, "updated_at": "2026-10-27",
            "repositories": {"Mapped Plugin": "curated/upstream", "Other": "curated/other"} if repositories is None else repositories}


def encoded(value):
    return json.dumps(value).encode()


class RepositoryMapStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundled, self.downloaded = self.root / "bundled.json", self.root / "downloaded.json"
        self.bundled.write_bytes(encoded(document()))
        self.service = RepositoryMap(self.bundled, self.downloaded)

    def test_newer_download_survives_reload_and_old_plugin_package(self):
        updated = document(8, {"Mapped Plugin": "corrected/repository"})
        self.assertTrue(self.service.install(encoded(updated)))
        reloaded = RepositoryMap(self.bundled, self.downloaded)
        self.assertEqual(reloaded.value, updated)
        self.assertEqual(reloaded.status()["source"], "downloaded")
        self.assertEqual(set(reloaded.value["repositories"]), {"Mapped Plugin"})
        self.assertEqual(json.loads(self.downloaded.read_bytes()), updated)

    def test_newer_bundle_wins_over_downloaded_snapshot(self):
        self.service.install(encoded(document(8)))
        newer = document(9, {})
        self.bundled.write_bytes(encoded(newer))
        self.assertEqual(RepositoryMap(self.bundled, self.downloaded).value, newer)

    def test_older_and_conflicting_equal_revisions_cannot_replace_current_map(self):
        self.service.install(encoded(document(8)))
        before = self.downloaded.read_bytes()
        for value in (document(6), document(8, {"Mapped Plugin": "wrong/source"})):
            with self.subTest(revision=value["revision"]), self.assertRaises(RuntimeError):
                self.service.install(encoded(value))
            self.assertEqual(self.downloaded.read_bytes(), before)
            self.assertEqual(self.service.value, document(8))
        self.assertFalse(self.service.install(before))

    def test_corrupt_download_falls_back_and_valid_equal_revision_repairs_it(self):
        for payload in (b"{broken", encoded(document(7, {"Mapped Plugin": "conflicting/repository"}))):
            self.downloaded.write_bytes(payload)
            service = RepositoryMap(self.bundled, self.downloaded)
            self.assertEqual(service.value, document())
            self.assertIsNotNone(service.status()["warning"])
            service.install(encoded(document()))
            self.assertIsNone(RepositoryMap(self.bundled, self.downloaded).status()["warning"])

    def test_invalid_remote_data_never_overwrites_the_last_good_snapshot(self):
        self.service.install(encoded(document(8)))
        before = self.downloaded.read_bytes()
        invalid = [b"{", b"\xff", b" " * (MAX_MAP_BYTES + 1),
                   b'{"schema_version":1,"revision":9,"updated_at":"2026-10-27","repositories":{"Same":"one/repo","Same":"another/repo"}}']
        for field, value in (("schema_version", 2), ("schema_version", True), ("revision", True), ("revision", 0),
                             ("updated_at", "20261027"), ("repositories", {"Bad": "https://example.org/repo"}),
                             ("repositories", {"Bad": "https://github.com/owner/repo"}), ("repositories", {" Name ": "owner/repo"}),
                             ("repositories", {"Bad": "SteamDeckHomebrew/decky-plugin-template"})):
            invalid.append(encoded({**document(9), field: value}))
        for payload in invalid:
            with self.subTest(payload=payload[:80]), self.assertRaises(RuntimeError):
                self.service.install(payload)
            self.assertEqual(self.downloaded.read_bytes(), before)
            self.assertEqual(self.service.value["revision"], 8)

    def test_failed_atomic_save_keeps_the_active_snapshot(self):
        with patch("sideload.repository_map.save_settings", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.service.install(encoded(document(8)))
        self.assertEqual(self.service.value, document())
        self.assertFalse(self.downloaded.exists())

    def test_multiple_names_can_share_a_monorepo_and_all_entries_can_be_removed(self):
        mono = document(8, {"First": "owner/monorepo", "Second": "owner/monorepo"})
        self.service.install(encoded(mono))
        self.assertEqual(self.service.value["repositories"], mono["repositories"])
        self.service.install(encoded(document(9, {})))
        self.assertEqual(RepositoryMap(self.bundled, self.downloaded).value["repositories"], {})


class RepositoryMapLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.folder = self.home / "plugins" / "different-folder"
        self.folder.mkdir(parents=True)
        (self.folder / "plugin.json").write_bytes(encoded({"name": "Mapped Plugin"}))
        (self.folder / "package.json").write_bytes(encoded({"name": "different-package", "version": "1.0.0", "repository": "declared/original"}))
        self.settings = self.home / "settings" / "self" / "settings.json"
        self.downloaded = self.home / "data" / "self" / "repository-map.json"
        bundled = self.home / "bundled.json"
        bundled.write_bytes(encoded(document()))
        self.decky = patch.multiple(main.decky, create=True, DECKY_HOME=str(self.home), DECKY_PLUGIN_NAME="Sideloader",
                                   DECKY_PLUGIN_RUNTIME_DIR=str(self.downloaded.parent), DECKY_PLUGIN_SETTINGS_DIR=str(self.settings.parent), logger=Mock())
        self.decky.start(); self.addCleanup(self.decky.stop)
        self.plugin = main.Plugin()
        self.plugin._repository_map = RepositoryMap(bundled, self.downloaded)
        self.native = patch.object(self.plugin, "_read_native_management", new=AsyncMock(return_value=({"ok": True, "plugins": []}, [])))
        self.native.start(); self.addCleanup(self.native.stop)
        self.transport = Mock(fetch_bytes=Mock(return_value=encoded(document(8))))
        self.transport_patch = patch.object(self.plugin, "_transport", return_value=self.transport)
        self.transport_patch.start(); self.addCleanup(self.transport_patch.stop)

    async def row(self):
        return (await self.plugin.list_sideload_plugins())["plugins"][0]

    async def test_first_read_uses_exact_registered_name_without_saving_or_network(self):
        row = await self.row()
        self.assertEqual((row["name"], row["repo"], row["repo_source"]), ("Mapped Plugin", "curated/upstream", "mapping"))
        self.assertTrue(row["ownership"]["eligible"])
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.settings.with_name("catalog.json").exists())
        self.transport.fetch_bytes.assert_not_called()
        (self.folder / "plugin.json").write_bytes(encoded({"name": "mapped plugin"}))
        self.assertEqual((await self.row())["repo"], "declared/original")

    async def test_missing_metadata_is_filled_and_table_deletion_returns_to_package(self):
        package = self.folder / "package.json"
        package.write_bytes(encoded({"name": "different-package", "version": "1.0.0"}))
        self.assertEqual((await self.row())["repo"], "curated/upstream")
        self.plugin._repository_map.install(encoded(document(8, {})))
        self.assertIsNone((await self.row())["repo"])
        package.write_bytes(encoded({"name": "different-package", "version": "1.0.0", "repository": "declared/original"}))
        self.assertEqual((await self.row())["repo"], "declared/original")

    async def test_manual_override_and_explicit_unlink_survive_table_updates(self):
        await self.plugin.set_binding("Mapped Plugin", "chosen/fork")
        self.plugin._repository_map.install(encoded(document(8, {"Mapped Plugin": "new/default"})))
        self.assertEqual(((await self.row())["repo"], (await self.row())["repo_source"]), ("chosen/fork", "user"))
        await self.plugin.clear_binding("Mapped Plugin")
        self.plugin._repository_map.install(encoded(document(9)))
        row = await self.row()
        self.assertEqual((row["repo"], row["repo_source"]), (None, "disabled"))
        self.assertEqual(load_settings(str(self.settings))["bindings"], {"Mapped Plugin": {"disabled": True}})

    async def test_explicit_remembered_repository_is_not_overridden_by_defaults(self):
        raw = read_plugins_dir(str(self.home / "plugins"))[0]
        self.plugin._catalog_service().remember(raw, "confirmed/fork", "user")
        self.assertEqual((await self.row())["repo"], "confirmed/fork")

    async def test_mapping_does_not_grant_store_or_unknown_management_permission(self):
        for native, capabilities, expected in (({"ok": True, "plugins": [{"name": "Mapped Plugin"}]}, ["Mapped Plugin"], "store"),
                                                ({"ok": False, "error": "offline"}, None, "unknown")):
            with patch.object(self.plugin, "_read_native_management", new=AsyncMock(return_value=(native, capabilities))):
                row = await self.row()
                self.assertEqual(row["repo"], "curated/upstream")
                self.assertEqual(row["ownership"]["state"], expected)
                self.assertFalse(row["ownership"]["eligible"])
                with self.assertRaises(RuntimeError):
                    await self.plugin.authorize_plugin_mutation("Mapped Plugin")

    async def test_owned_repository_and_its_skip_rules_win_over_mapping(self):
        loader = self.home / "settings" / "loader.json"
        loader.parent.mkdir(parents=True, exist_ok=True)
        loader.write_bytes(encoded({"frozenPlugins": ["Mapped Plugin"]}))
        self.plugin._ownership().save({"version": 1, "entries": [{"id": "a" * 32, "name": "Mapped Plugin", "folder": self.folder.name,
            "repo": "confirmed/store-fork", "prior_frozen": False, "action": "takeover", "state": "active"}]})
        save_settings(str(self.settings), {"skippedReleases": {"Mapped Plugin": {"repo": "confirmed/store-fork", "tags": ["v2"]}}})
        row = await self.row()
        self.assertEqual((row["repo"], row["ownership"]["state"], row["skipped_tags"]), ("confirmed/store-fork", "owned", ["v2"]))

    async def test_duplicate_name_and_changed_binding_remain_blocked(self):
        await self.plugin.set_binding("Mapped Plugin", "chosen/fork")
        package = self.folder / "package.json"
        package.write_bytes(encoded({"name": "replacement-package", "version": "2.0.0"}))
        self.assertFalse((await self.row())["ownership"]["eligible"])
        duplicate = self.home / "plugins" / "duplicate"
        duplicate.mkdir()
        (duplicate / "plugin.json").write_bytes(encoded({"name": "Mapped Plugin"}))
        rows = (await self.plugin.list_sideload_plugins())["plugins"]
        self.assertEqual([row["ownership"]["state"] for row in rows], ["readonly", "readonly"])

    async def test_update_activates_complete_snapshot_and_preserves_user_settings(self):
        await self.plugin.set_auto_check(True, 720)
        before = self.settings.read_bytes()
        self.transport.fetch_bytes.return_value = encoded(document(8, {"Mapped Plugin": "corrected/source"}))
        result = await self.plugin.update_repository_map()
        self.assertTrue(result["changed"])
        self.assertEqual((await self.row())["repo"], "corrected/source")
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertNotIn("Other", self.plugin._repository_map.value["repositories"])

    async def test_network_failure_retains_last_good_map_and_binding(self):
        await self.plugin.set_binding("Mapped Plugin", "chosen/fork")
        before = self.settings.read_bytes()
        self.transport.fetch_bytes.side_effect = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await self.plugin.update_repository_map()
        self.assertEqual((await self.plugin.get_repository_map_status())["revision"], 7)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(self.downloaded.exists())

    async def blocked_update(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def fetch(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test worker was not released")
            return encoded(document(8))
        self.transport.fetch_bytes.side_effect = fetch
        task = asyncio.create_task(self.plugin.update_repository_map())
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        return task, release

    async def test_cancelled_rpc_does_not_orphan_or_duplicate_the_update(self):
        first, release = await self.blocked_update()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(self.plugin.update_repository_map())
        release.set()
        result = await second
        self.assertEqual(result["revision"], 8)
        self.assertEqual(json.loads(self.downloaded.read_bytes())["revision"], 8)
        self.transport.fetch_bytes.assert_called_once()

    async def test_task_or_check_started_during_download_prevents_activation(self):
        for busy in ("installation", "check"):
            with self.subTest(busy=busy):
                task, release = await self.blocked_update()
                if busy == "installation":
                    self.plugin._flow = types.SimpleNamespace(operation={"stage": "ready", "existing_name": None, "package": None, "target": None})
                else:
                    self.plugin._check_lock = asyncio.Lock()
                    await self.plugin._check_lock.acquire()
                release.set()
                with self.assertRaises(RuntimeError):
                    await task
                self.assertEqual(self.plugin._repository_map.value["revision"], 7)
                self.assertFalse(self.downloaded.exists())
                if busy == "installation":
                    del self.plugin._flow
                else:
                    self.plugin._check_lock.release()

    async def test_unload_drains_fetch_and_prevents_a_late_snapshot_write(self):
        task, release = await self.blocked_update()
        unload = asyncio.create_task(self.plugin._unload())
        await asyncio.sleep(0)
        self.assertFalse(unload.done())
        release.set()
        with self.assertRaises(RuntimeError):
            await task
        await unload
        self.assertFalse(self.downloaded.exists())
        self.assertEqual(self.plugin._repository_map.value["revision"], 7)
