import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
import sideload
from sideload.github_meta import compare_versions, parse_github_input
from sideload.persist import apply_bindings, load_settings


class QueryContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.home = home
        for name in ("One", "Two", "Unbound"):
            folder = home / "plugins" / name
            folder.mkdir(parents=True)
            (folder / "plugin.json").write_text(json.dumps({"name": name}))
            package = {"version": "1.0.0"}
            if name != "Unbound":
                package["repository"] = f"https://github.com/acme/{name}"
            (folder / "package.json").write_text(json.dumps(package))
        self.decky = patch.multiple(main.decky, create=True, DECKY_HOME=str(home), DECKY_PLUGIN_NAME="decky-sideloader", DECKY_PLUGIN_RUNTIME_DIR=str(home / "data" / "self"), DECKY_PLUGIN_SETTINGS_DIR=str(home / "settings" / "self"), logger=Mock())
        self.decky.start()
        self.addCleanup(self.decky.stop)
        self.store = patch.object(sideload.http_client, "_fetch_store_names", return_value=({"StoreOnly"}, None))
        self.store.start()
        self.addCleanup(self.store.stop)
        self.registry = patch.object(sideload.http_client.GitHubTransport, "fetch_bytes", return_value=b'[submodule "plugins/other"]\n path = plugins/other\n url = https://github.com/acme/other\n')
        self.registry.start()
        self.addCleanup(self.registry.stop)
        native = patch.object(main.Plugin, "_read_native_management", new=AsyncMock(return_value=({"ok": True, "plugins": []}, [])))
        native.start(); self.addCleanup(native.stop)
        self.plugin = main.Plugin()
        # Tests of release/notification contracts begin with an explicit source
        # refresh; ordinary reads and update checks no longer fetch the store.
        await self.plugin.list_sideload_plugins(True)

    async def test_poll_respects_auto_check_and_existing_last_check_interval(self):
        import asyncio
        for enabled, checked in ((False, False), (True, False), (True, True)):
            await self.plugin.set_auto_check(enabled, 360)
            if checked:
                with patch.object(self.plugin, "_release_reader", return_value=lambda *args: []):
                    await self.plugin.check_updates()
            with patch.object(main.asyncio, "sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])), patch.object(self.plugin, "_scheduled_check", new=AsyncMock()) as scheduled:
                with self.assertRaises(asyncio.CancelledError):
                    await self.plugin._poll_loop()
                self.assertEqual(scheduled.await_count, int(enabled and not checked))

    async def test_notification_rpc_preserves_other_settings_and_rejects_non_bool(self):
        before = await self.plugin.get_app_settings()
        self.assertTrue(before["checkNotifications"])
        after = await self.plugin.set_check_notifications(False)
        self.assertFalse(after.pop("checkNotifications"))
        before.pop("checkNotifications")
        self.assertEqual(after, before)
        for value in (0, 1, "false", None):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_check_notifications(value)
        self.assertFalse((await self.plugin.get_app_settings())["checkNotifications"])

    async def test_home_task_notice_rpc_preserves_other_settings_and_rejects_non_bool(self):
        before = await self.plugin.get_app_settings()
        self.assertTrue(before["showHomeTaskNotice"])
        after = await self.plugin.set_show_home_task_notice(False)
        self.assertFalse(after.pop("showHomeTaskNotice"))
        before.pop("showHomeTaskNotice")
        self.assertEqual(after, before)
        for value in (0, 1, "false", None):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_show_home_task_notice(value)
        await self.plugin.set_auto_check(True, 360)
        await self.plugin.set_check_notifications(False)
        self.assertFalse((await self.plugin.get_app_settings())["showHomeTaskNotice"])

    async def test_delete_package_setting_preserves_other_settings_and_rejects_non_bool(self):
        before = await self.plugin.get_app_settings()
        self.assertTrue(before["deletePackageAfterInstall"])
        after = await self.plugin.set_delete_package_after_install(False)
        self.assertFalse(after.pop("deletePackageAfterInstall"))
        before.pop("deletePackageAfterInstall")
        self.assertEqual(after, before)
        for value in (0, 1, "true", None):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_delete_package_after_install(value)
        self.assertFalse((await self.plugin.get_app_settings())["deletePackageAfterInstall"])



    async def test_scheduled_reports_emit_once_for_all_outcomes_and_disabled_notifications(self):
        for outcome in ("update", "current", "partial", "whole"):
            for notify in (True, False):
                await self.plugin.set_check_notifications(notify)
                def releases(repo, version):
                    if outcome == "partial" and repo.endswith("Two"):
                        raise RuntimeError("release failed")
                    return [{"tag": "2.0.0" if outcome == "update" else "1.0.0", "has_zip": True, "comparison": 1 if outcome == "update" else 0}]
                with patch.object(main.decky, "emit", new=AsyncMock(), create=True) as emit, patch.object(self.plugin, "_release_reader", return_value=releases):
                    if outcome == "whole":
                        with patch.object(self.plugin, "list_sideload_plugins", side_effect=RuntimeError("listing failed")):
                            await self.plugin._scheduled_check()
                    else:
                        await self.plugin._scheduled_check()
                    emit.assert_awaited_once()
                    name, payload = emit.call_args.args
                self.assertEqual(name, "scheduled_check_completed")
                self.assertEqual(payload["checkNotifications"], notify)
                report = payload["report"]
                self.assertEqual(report["source"], "scheduled")
                persisted = await self.plugin.get_app_settings()
                self.assertEqual(persisted["lastCheck"], report)
                self.assertEqual(persisted["lastScheduledCheck"], report)
                if outcome == "whole":
                    self.assertEqual(report["error"], "listing failed")
                elif outcome == "partial":
                    self.assertEqual(sum(i["status"] == "error" for i in report["items"]), 1)
                else:
                    self.assertEqual(sum(i["status"] == outcome for i in report["items"]), 2)
                with patch.object(self.plugin, "_release_reader", return_value=releases):
                    await self.plugin.check_updates()
                self.assertEqual((await self.plugin.get_app_settings())["lastScheduledCheck"], report)
                self.assertEqual((await self.plugin.get_app_settings())["lastCheck"]["source"], "manual")

    async def test_scheduled_uses_completion_preference_and_delivery_failure_does_not_change_report(self):
        async def listing():
            await self.plugin.set_check_notifications(False)
            return {"plugins": []}
        with patch.object(self.plugin, "list_sideload_plugins", side_effect=listing), patch.object(main.decky, "emit", new=AsyncMock(side_effect=RuntimeError("delivery failed")), create=True) as emit:
            await self.plugin._scheduled_check()
        self.assertFalse(emit.call_args.args[1]["checkNotifications"])
        saved = (await self.plugin.get_app_settings())["lastScheduledCheck"]
        self.assertNotIn("error", saved)
        self.assertEqual(saved, emit.call_args.args[1]["report"])

    async def test_repository_search_returns_choices_without_saving_a_binding(self):
        before = await self.plugin.get_app_settings()
        item = {"full_name": "acme/decky-example", "description": "A Decky plugin", "stargazers_count": 42, "fork": True, "archived": False}
        with patch.object(sideload.http_client, "_fetch_json", return_value={"items": [item]}) as fetch:
            result = await self.plugin.search_repositories("  alpha & decky  ")
        self.assertEqual(result, [{"repo": "acme/decky-example", "description": "A Decky plugin", "stars": 42, "fork": True, "archived": False}])
        url = urlsplit(fetch.call_args.args[0])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "api.github.com", "/search/repositories"))
        self.assertEqual(parse_qs(url.query), {"q": ["alpha & decky fork:true"], "per_page": ["20"]})
        self.assertEqual(fetch.call_args.kwargs["accept"], "application/vnd.github+json")
        self.assertEqual(await self.plugin.get_app_settings(), before)

    async def test_repository_search_rejects_invalid_queries_without_requesting(self):
        with patch.object(sideload.http_client, "_fetch_json") as fetch:
            for query in ("", "   ", "x" * 101, None, 42):
                with self.assertRaises(RuntimeError):
                    await self.plugin.search_repositories(query)
            fetch.assert_not_called()

    async def test_repository_search_filters_invalid_rows_and_bounds_results(self):
        items = [{"full_name": "../bad"}, None, {"full_name": "acme/one", "description": None}, {"full_name": "acme/one"}]
        with patch.object(sideload.http_client, "_fetch_json", return_value={"items": items}):
            self.assertEqual(await self.plugin.search_repositories("example"), [{"repo": "acme/one", "description": "", "stars": 0, "fork": False, "archived": False}])
        with patch.object(sideload.http_client, "_fetch_json", return_value={"items": [{"full_name": f"acme/repo-{n}"} for n in range(25)]}):
            self.assertEqual(len(await self.plugin.search_repositories("example")), 20)

    async def test_repository_search_distinguishes_empty_invalid_and_failed_responses(self):
        with patch.object(sideload.http_client, "_fetch_json", return_value={"items": []}):
            self.assertEqual(await self.plugin.search_repositories("example"), [])
        for payload in ([], {}, {"items": None}):
            with patch.object(sideload.http_client, "_fetch_json", return_value=payload), self.assertRaises(RuntimeError):
                await self.plugin.search_repositories("example")
        with patch.object(sideload.http_client, "_fetch_json", side_effect=RuntimeError("GitHub 请求已限流")), self.assertRaisesRegex(RuntimeError, "限流"):
            await self.plugin.search_repositories("example")

    async def test_repository_search_preserves_explicit_fork_filter(self):
        with patch.object(sideload.http_client, "_fetch_json", return_value={"items": []}) as fetch:
            await self.plugin.search_repositories("decky fork:only")
        self.assertEqual(parse_qs(urlsplit(fetch.call_args.args[0]).query)["q"], ["decky fork:only"])

    async def test_probe_cache_is_bounded_and_expires_without_hiding_failures(self):
        proof = {"repo": "acme/example", "tree_sha": "a" * 40, "plugins": []}
        with patch.object(sideload.releases_api, "probe_repository", return_value=proof) as probe, patch.object(sideload.releases_api, "time", types.SimpleNamespace(monotonic=lambda: 100)):
            self.assertEqual(await self.plugin.probe_repository("https://github.com/acme/example"), proof)
            await self.plugin.probe_repository("acme/EXAMPLE")
            probe.assert_called_once()
        with patch.object(sideload.releases_api, "probe_repository", side_effect=RuntimeError("offline")), patch.object(sideload.releases_api, "time", types.SimpleNamespace(monotonic=lambda: 401)):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                await self.plugin.probe_repository("acme/example")
        with patch.object(sideload.releases_api, "probe_repository", return_value=proof) as probe:
            for i in range(11):
                await self.plugin.probe_repository(f"acme/repo-{i}")
            self.assertEqual(len(self.plugin._probe_cache), 10)

    async def test_new_install_cannot_bypass_probe_or_guess_a_monorepo_project(self):
        candidates = [{"path": "plugins/a", "name": "A", "package_name": "a"}, {"path": "plugins/b", "name": "B", "package_name": "b"}]
        flow = Mock(prepare=AsyncMock(return_value={"stage": "checking"}))
        with patch.object(self.plugin, "_install_flow", return_value=flow), patch.object(self.plugin, "probe_repository", new=AsyncMock(return_value={"tree_sha": "a" * 40, "plugins": []})):
            with self.assertRaisesRegex(RuntimeError, "未确认"):
                await self.plugin.prepare_install("acme/mono", "v1", "https://github.com/acme/mono/releases/download/v1/a.zip")
            flow.prepare.assert_not_called()
        with patch.object(self.plugin, "_install_flow", return_value=flow), patch.object(self.plugin, "probe_repository", new=AsyncMock(return_value={"tree_sha": "a" * 40, "plugins": candidates})):
            for path in (None, "../a", "missing"):
                with self.assertRaises(RuntimeError):
                    await self.plugin.prepare_install("acme/mono", "v1", "url", None, path)
            await self.plugin.prepare_install("acme/mono", "v1", "url", None, "plugins/b")
        self.assertEqual(flow.prepare.call_args.args[-1], {**candidates[1], "tree_sha": "a" * 40})

    async def test_existing_plugin_updates_preserve_their_existing_identity_guard(self):
        flow = Mock(prepare=AsyncMock(return_value={"stage": "checking"}))
        with patch.object(self.plugin, "_install_flow", return_value=flow), patch.object(self.plugin, "probe_repository", new=AsyncMock()) as probe:
            await self.plugin.prepare_install("acme/One", "v1", "url", "One")
            probe.assert_not_called()
        self.assertIsNone(flow.prepare.call_args.args[-1])

    async def test_failed_check_is_not_latest_and_report_survives_reload(self):
        def release(repo, current, **_kwargs):
            if repo.endswith("Two"):
                raise RuntimeError("HTTP 403 GitHub rate limit")
            return [{"tag": "v1.1.0", "has_zip": True, "comparison": 1}]
        with patch.object(sideload.releases_api, "_releases_for", side_effect=release):
            response = await self.plugin.check_updates()
        by_name = {r["name"]: r for r in response["last_check"]["items"]}
        self.assertEqual(by_name["One"]["status"], "update")
        self.assertEqual(by_name["Two"]["status"], "error")
        self.assertEqual(by_name["Unbound"]["status"], "unbound")
        self.assertEqual((await self.plugin.get_app_settings())["lastCheck"], response["last_check"])

    async def test_latest_without_asset_uses_the_newest_packaged_release(self):
        with patch.object(sideload.releases_api, "_releases_for", return_value=[
            {"tag": "v3", "has_zip": False, "comparison": 1},
            {"tag": "v2", "has_zip": True, "comparison": 1},
        ]):
            response = await self.plugin.check_updates()
        item = response["last_check"]["items"][0]
        self.assertEqual(item["status"], "update")
        self.assertEqual(item["latest"], "2")
        self.assertIn("v3", item["notice"])
        self.assertIn("没有安装包", item["notice"])

    async def test_detail_check_updates_only_current_plugin_and_survives_reload(self):
        with patch.object(sideload.releases_api, "_releases_for", return_value=[{"tag": "v2", "has_zip": True}]):
            before = (await self.plugin._check_updates("scheduled"))["last_check"]
        with patch.object(sideload.releases_api, "_releases_for", return_value=[{"tag": "v1.0.0", "has_zip": True}]) as reader:
            await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One", True)
        self.assertEqual(reader.call_count, 1)
        after = (await main.Plugin().list_sideload_plugins())["last_check"]
        one = next(item for item in after["items"] if item["name"] == "One")
        self.assertEqual((one["status"], one["current"], one["latest"]), ("current", "1.0.0", "1.0.0"))
        self.assertEqual([item for item in after["items"] if item["name"] != "One"], [item for item in before["items"] if item["name"] != "One"])
        self.assertEqual(after["fullCheckedAt"], before["checkedAt"])
        self.assertEqual((await self.plugin.get_app_settings())["lastScheduledCheck"], before)

    async def test_detail_failure_replaces_old_success_without_claiming_latest(self):
        with patch.object(sideload.releases_api, "_releases_for", return_value=[{"tag": "v1.0.0", "has_zip": True}]):
            success = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        with patch.object(sideload.releases_api, "_releases_for", side_effect=RuntimeError("GitHub offline")):
            failed = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One", True)
        self.assertEqual(failed["error"], "GitHub offline")
        self.assertEqual(failed["snapshot"], success["snapshot"])
        report = (await self.plugin.list_sideload_plugins())["last_check"]
        self.assertEqual([(item["name"], item["status"], item["latest"]) for item in report["items"]], [("One", "error", None)])

    async def test_detail_completion_does_not_publish_for_changed_local_version(self):
        before = (await self.plugin.get_app_settings())["lastCheck"]
        def releases(*args, **kwargs):
            (self.home / "plugins/One/package.json").write_text(json.dumps({"version": "2.0.0", "repository": "https://github.com/acme/One"}))
            return [{"tag": "v2.0.0", "has_zip": True}]
        with patch.object(sideload.releases_api, "_releases_for", side_effect=releases):
            with self.assertRaisesRegex(RuntimeError, "已变化"):
                await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        self.assertEqual((await self.plugin.get_app_settings())["lastCheck"], before)

    async def test_detail_check_does_not_postpone_first_automatic_full_check(self):
        import asyncio
        await self.plugin.set_auto_check(True, 360)
        with patch.object(sideload.releases_api, "_releases_for", return_value=[]):
            await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        with patch.object(main.asyncio, "sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])), patch.object(self.plugin, "_scheduled_check", new=AsyncMock()) as scheduled:
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin._poll_loop()
            scheduled.assert_awaited_once()

    async def test_unlink_suppresses_metadata_and_rebind_restores(self):
        await self.plugin.clear_binding("One")
        listing = await self.plugin.list_sideload_plugins()
        one = next(p for p in listing["plugins"] if p["name"] == "One")
        self.assertIsNone(one["repo"])
        self.assertEqual(one["repo_source"], "disabled")
        await self.plugin.set_binding("One", "acme/Another")
        listing = await self.plugin.list_sideload_plugins()
        self.assertEqual(next(p for p in listing["plugins"] if p["name"] == "One")["repo"], "acme/Another")

    async def test_store_failure_keeps_user_managed_plugin(self):
        await self.plugin.set_binding("One", "acme/One")
        # Binding validation now reads membership; explicitly model unavailable
        # catalog state rather than accidentally reusing its fresh success cache.
        self.plugin._catalog = sideload.catalog.Catalog(Path(sideload.paths._settings_path()).with_name("offline-catalog.json"), lambda: (set(), "offline"), lambda: "")
        with patch.object(sideload.http_client, "_fetch_store_names", return_value=(set(), "offline")):
            listing = await self.plugin.list_sideload_plugins()
        self.assertFalse(listing["store_ok"])
        self.assertTrue(next(p for p in listing["plugins"] if p["name"] == "One")["sideload"])
        self.assertIsNone(listing["plugins"][0]["in_store"])

    async def test_network_mode_defaults_and_validation_preserve_existing_settings(self):
        self.assertEqual((await self.plugin.get_app_settings())["networkMode"], "direct")
        await self.plugin.set_binding("One", "acme/One")
        for mode in ("auto", "gh-proxy", "ghfast", "direct"):
            result = await self.plugin.set_network_mode(mode)
            self.assertEqual(result["networkMode"], mode)
            self.assertEqual(result["bindings"]["One"]["repo"], "acme/One")
            self.assertEqual((await main.Plugin().get_app_settings())["networkMode"], mode)
        for mode in ("https://evil.test", "proxy", None, {}):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_network_mode(mode)

    async def test_search_release_and_probe_queries_share_selected_transport(self):
        await self.plugin.set_network_mode("gh-proxy")
        modes, urls = [], []
        def fetch(transport, url, **kwargs):
            modes.append(transport.mode); urls.append(url)
            return {"items": []} if "/search/" in url else []
        def probe(repo, fetcher):
            fetcher("https://api.github.com/repos/acme/One/git/trees/HEAD", max_bytes=100)
            fetcher("https://api.github.com/repos/acme/One/git/blobs/abc", max_bytes=100)
            return {"repo": repo, "tree_sha": "a" * 40, "plugins": []}
        with patch.object(sideload.http_client.GitHubTransport, "fetch_json", autospec=True, side_effect=fetch), patch.object(sideload.releases_api, "probe_repository", side_effect=probe):
            await self.plugin.search_repositories("Example")
            await self.plugin.list_releases("acme/One")
            await self.plugin.probe_repository("acme/One")
        self.assertEqual(modes, ["gh-proxy"] * 4)
        self.assertTrue(any("git/trees" in url for url in urls))
        self.assertTrue(any("git/blobs" in url for url in urls))

    async def test_registry_fallback_keeps_membership_unknown_and_reports_explicit_registration(self):
        await self.plugin.set_binding("One", "acme/One")
        # Binding validation now reads membership; explicitly model unavailable
        # catalog state rather than accidentally reusing its fresh success cache.
        self.plugin._catalog = sideload.catalog.Catalog(Path(sideload.paths._settings_path()).with_name("offline-catalog.json"), lambda: (set(), "timeout"), lambda: "")
        with patch.object(sideload.http_client, "_fetch_store_names", return_value=(set(), "timeout")), patch.object(sideload.http_client.GitHubTransport, "fetch_bytes", return_value=b'[submodule "plugins/One"]\n path = plugins/One\n url = https://github.com/acme/One\n'):
            with self.assertRaisesRegex(RuntimeError, "刷新失败"):
                await self.plugin.list_sideload_plugins(True)
            listing = await self.plugin.list_sideload_plugins()
        self.assertFalse(listing["store_ok"])
        self.assertEqual(listing["catalog"]["source"], "registry")
        self.assertIsNone(listing["store_error"])
        one = next(p for p in listing["plugins"] if p["name"] == "One")
        self.assertEqual(one["registration"], "registered")
        self.assertIsNone(one["in_store"])
        self.assertTrue(one["sideload"])
        self.assertEqual(next(p for p in listing["plugins"] if p["name"] == "Two")["registration"], "unknown")

    async def test_settings_interval_is_validated_and_retained(self):
        with self.assertRaises(RuntimeError):
            await self.plugin.set_auto_check(True, 0)
        await self.plugin.set_auto_check(True, 720)
        self.assertEqual((await self.plugin.get_app_settings())["autoCheckMinutes"], 720)

    async def test_binding_requires_existing_nonself_plugin(self):
        for name in ("missing", "decky-sideloader"):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_binding(name, "acme/One")

    async def test_download_timeout_choices_preserve_other_settings(self):
        self.assertEqual((await self.plugin.get_app_settings())["downloadTimeoutMinutes"], 3)
        await self.plugin.set_binding("One", "acme/One")
        after = await self.plugin.set_download_timeout(10)
        self.assertEqual(after["downloadTimeoutMinutes"], 10)
        self.assertEqual(after["bindings"]["One"]["repo"], "acme/One")
        for minutes in (0, 4, "10", None):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_download_timeout(minutes)
        self.assertEqual((await self.plugin.get_app_settings())["downloadTimeoutMinutes"], 10)

    async def test_skipped_release_is_remembered_and_ignored_by_the_next_check(self):
        listing = await self.plugin.set_skipped_release("One", "2.0.0", True)
        one = next(plugin for plugin in listing["plugins"] if plugin["name"] == "One")
        self.assertEqual(one["skipped_tags"], ["2.0.0"])
        with patch.object(sideload.releases_api, "_releases_for", return_value=[
            {"tag": "2.0.0", "has_zip": True, "repo": "acme/One"},
            {"tag": "1.0.0", "has_zip": True, "repo": "acme/One"},
        ]):
            response = await self.plugin.check_updates()
        item = next(row for row in response["last_check"]["items"] if row["name"] == "One")
        self.assertEqual(item["status"], "current")
        self.assertEqual(item["release_tag"], "1.0.0")
        self.assertIn("已跳过 2.0.0", item["notice"])
        cleared = await self.plugin.set_skipped_release("One", "2.0.0", False)
        self.assertEqual(next(plugin for plugin in cleared["plugins"] if plugin["name"] == "One")["skipped_tags"], [])
        for value in (0, "true", None):
            with self.assertRaises(RuntimeError):
                await self.plugin.set_skipped_release("One", "2.0.0", value)

    async def test_skip_changes_recalculate_offline_and_preserve_cached_package_version_after_reload(self):
        rows = sideload.releases_api._releases_for("acme/One", "1.0.0", fetch=lambda *args, **kwargs: [
            {"tag_name": tag, "published_at": stamp, "body": "complete notes", "assets": [{"name": "plugin.zip", "browser_download_url": f"https://github.com/acme/One/releases/download/{tag}/plugin.zip"}]}
            for tag, stamp in (("v9.0.0", "2026-10-25T00:00:00Z"), ("v1.0.0", "2026-10-24T00:00:00Z"))
        ])
        with patch.object(sideload.releases_api, "_releases_for", return_value=rows), patch.object(sideload.releases_api, "probe_release_zip", return_value=("semver", "2.0.0")):
            original = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        original_report = (await self.plugin.get_app_settings())["lastCheck"]
        self.plugin = main.Plugin()
        with patch.object(sideload.releases_api, "_releases_for", side_effect=AssertionError("skip must not fetch releases")), patch.object(sideload.releases_api, "probe_release_zip", side_effect=AssertionError("skip must not probe ZIP")), patch.object(sideload.releases_api, "git_tree_version", side_effect=AssertionError("skip must not read git")):
            for tag, skipped, status, latest in (("v9.0.0", True, "current", "1.0.0"), ("v1.0.0", True, "current", None), ("v9.0.0", False, "update", "2.0.0")):
                listing = await self.plugin.set_skipped_release("One", tag, skipped)
                item = next(row for row in listing["last_check"]["items"] if row["name"] == "One")
                self.assertEqual((item["status"], item["latest"]), (status, latest))
                self.assertEqual(item["checkedAt"], original["snapshot"]["checkedAt"])
                self.assertEqual(listing["last_check"]["checkedAt"], original_report["checkedAt"])
                self.assertEqual(listing["last_check"]["fullCheckedAt"], original_report["fullCheckedAt"])
                self.assertEqual(await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One"), original)
        self.assertEqual((await self.plugin.get_app_settings())["lastScheduledCheck"], None)

    async def test_skip_during_inflight_read_shares_request_and_uses_current_policy(self):
        import asyncio
        entered, resume = asyncio.Event(), asyncio.Event()
        original = self.plugin._check_plugin_release
        async def blocked(plugin):
            entered.set()
            await resume.wait()
            return await original(plugin)
        with patch.object(self.plugin, "_check_plugin_release", side_effect=blocked), patch.object(sideload.releases_api, "_releases_for", return_value=[{"tag": "v2", "has_zip": True}]) as reader:
            first = asyncio.create_task(self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One"))
            await entered.wait()
            await self.plugin.set_skipped_release("One", "v2", True)
            second = asyncio.create_task(self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One"))
            await asyncio.sleep(0)
            resume.set()
            results = await asyncio.gather(first, second)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(results[0], results[1])
        item = (await self.plugin.get_app_settings())["lastCheck"]["items"][0]
        self.assertEqual(item["status"], "current")
        self.assertIsNone(item["latest"])

    async def test_full_check_cannot_overwrite_a_skip_changed_while_another_plugin_is_reading(self):
        import asyncio
        entered, resume = asyncio.Event(), asyncio.Event()
        original = self.plugin._check_plugin_release
        async def blocked(plugin):
            if plugin["name"] == "Two":
                entered.set()
                await resume.wait()
            return await original(plugin)
        with patch.object(self.plugin, "_check_plugin_release", side_effect=blocked), patch.object(sideload.releases_api, "_releases_for", return_value=[{"tag": "v2", "has_zip": True}]):
            full = asyncio.create_task(self.plugin.check_updates())
            await entered.wait()
            await self.plugin.set_skipped_release("One", "v2", True)
            resume.set()
            listing = await full
        one = next(item for item in listing["last_check"]["items"] if item["name"] == "One")
        two = next(item for item in listing["last_check"]["items"] if item["name"] == "Two")
        self.assertEqual(one["status"], "current")
        self.assertIsNone(one["latest"])
        self.assertEqual(two["status"], "update")

    async def test_skip_does_not_clear_a_failed_refresh_or_claim_a_new_check(self):
        with patch.object(sideload.releases_api, "_releases_for", return_value=[]):
            cached = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        with patch.object(sideload.releases_api, "_releases_for", side_effect=RuntimeError("offline")):
            await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One", True)
        before = (await self.plugin.get_app_settings())["lastCheck"]
        with patch.object(sideload.releases_api, "_releases_for", side_effect=AssertionError("must stay offline")):
            after = await self.plugin.set_skipped_release("One", "v2", True)
            result = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        self.assertEqual(after["last_check"], before)
        self.assertEqual(result["error"], "offline")
        self.assertEqual(result["snapshot"], cached["snapshot"])

    async def test_successful_empty_snapshot_survives_reload_without_network(self):
        with patch.object(sideload.releases_api, "_releases_for", return_value=[]):
            checked = await self.plugin.check_updates()
        with patch.object(sideload.releases_api, "_releases_for", side_effect=AssertionError("cached read must stay offline")):
            reloaded = main.Plugin()
            listed = await reloaded.list_sideload_plugins()
            result = await reloaded.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        self.assertEqual(result["snapshot"]["releases"], [])
        self.assertIn(result["snapshot"], listed["release_snapshots"])
        self.assertEqual(listed["last_check"], checked["last_check"])

    async def test_same_identity_concurrent_reads_share_network_work(self):
        import asyncio
        entered, resume = asyncio.Event(), asyncio.Event()
        original = self.plugin._check_plugin_release
        async def blocked(plugin):
            entered.set()
            await resume.wait()
            return await original(plugin)
        with patch.object(self.plugin, "_check_plugin_release", side_effect=blocked), patch.object(sideload.releases_api, "_releases_for", return_value=[]) as reader:
            first = asyncio.create_task(self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One"))
            await entered.wait()
            second = asyncio.create_task(self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One", True))
            await asyncio.sleep(0)
            resume.set()
            results = await asyncio.gather(first, second)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(results[0], results[1])

    async def test_detail_queued_behind_full_check_reuses_its_success(self):
        import asyncio
        entered, resume = asyncio.Event(), asyncio.Event()
        original = self.plugin._check_plugin_release
        async def blocked(plugin):
            if plugin["name"] == "One":
                entered.set()
                await resume.wait()
            return await original(plugin)
        with patch.object(self.plugin, "_check_plugin_release", side_effect=blocked), patch.object(sideload.releases_api, "_releases_for", return_value=[]) as reader:
            full = asyncio.create_task(self.plugin.check_updates())
            await entered.wait()
            detail = asyncio.create_task(self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One", True))
            await asyncio.sleep(0)
            resume.set()
            checked, result = await asyncio.gather(full, detail)
        self.assertEqual(reader.call_count, 2)
        self.assertIn(result["snapshot"], checked["release_snapshots"])

    async def test_failed_refresh_keeps_last_success_after_backend_restart(self):
        with patch.object(sideload.releases_api, "_releases_for", return_value=[]):
            success = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One")
        with patch.object(sideload.releases_api, "_releases_for", side_effect=RuntimeError("offline")):
            failed = await self.plugin.get_plugin_releases("acme/One", "1.0.0", "One", "One", True)
        with patch.object(sideload.releases_api, "_releases_for", side_effect=AssertionError("cache must stay offline")):
            reloaded = await main.Plugin().get_plugin_releases("acme/One", "1.0.0", "One", "One")
        self.assertEqual(failed["snapshot"], success["snapshot"])
        self.assertEqual(reloaded, failed)
        self.assertEqual(reloaded["error"], "offline")



class PureContractTests(unittest.TestCase):
    def test_comparison_handles_reinstall_and_prereleases(self):
        self.assertEqual(compare_versions("v1.0.0", "1.0"), 0)
        self.assertEqual(compare_versions("1.0.0-beta.2", "1.0.0-beta.10"), -1)
        self.assertEqual(compare_versions("1.0.0", "1.0.0-beta.2"), 1)
        for value in ("1.2foo", "nightly", "1.2.3.4", "1.0.0-beta.01"):
            self.assertIsNone(compare_versions(value, "1.0.0"))

    def test_release_rows_keep_the_repository_used_for_install(self):
        payload = [{"tag_name": "v0.3.0", "draft": False, "prerelease": False, "published_at": "2026-04-06T00:00:00Z",
                    "assets": [{"name": "DLSS.Enabler.zip", "browser_download_url": "https://github.com/acme/dlss/releases/download/v0.3.0/DLSS.Enabler.zip"}]}]
        rows = sideload.releases_api._releases_for("acme/dlss", "0.2.1", fetch=lambda _url, **_kwargs: payload)
        self.assertEqual(rows[0]["repo"], "acme/dlss")
        self.assertEqual(rows[0]["tag"], "v0.3.0")
        self.assertTrue(rows[0]["has_zip"])

    def test_repository_parser_does_not_accept_embedded_github_hosts(self):
        for value in ("https://evil.test/github.com/acme/foo", "https://notgithub.com/acme/foo", "../foo", "acme/..", "https://github.com:444/acme/foo"):
            self.assertIsNone(parse_github_input(value), value)
        self.assertEqual(parse_github_input("github.com/acme/foo"), "acme/foo")

    def test_legacy_binding_preserves_repo_without_granting_store_ownership(self):
        result = apply_bindings([{"name": "One", "repo": None, "sideload": False, "is_self": False}], {"One": {"repo": "a/b"}})
        self.assertFalse(result[0]["sideload"])
        self.assertEqual(result[0]["repo"], "a/b")

    def test_malformed_settings_and_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "settings.json"
            file.write_text('{"autoCheckMinutes":true,"bindings":[],"lastCheck":null}')
            self.assertEqual(load_settings(str(file))["autoCheckMinutes"], 360)
