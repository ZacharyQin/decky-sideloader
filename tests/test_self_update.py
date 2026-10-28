import asyncio
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
from sideload.diagnostic_snapshot import build_snapshot
from sideload.self_update import FOLDER, NAME, REPOSITORY, select_release


def package(path, version, marker):
    bundle = f"export const build = {json.dumps(marker)};"
    fingerprint = hashlib.sha256(marker.encode()).hexdigest()
    files = {"plugin.json": json.dumps({"name": NAME, "flags": []}),
             "package.json": json.dumps({"name": FOLDER, "version": version}),
             "main.py": f"BUILD = {marker!r}\n", "py_modules/sideload/__init__.py": "",
             "dist/index.js": bundle, "LICENSE": "BSD-3-Clause",
             "dist/build.json": json.dumps({"version": version, "fingerprint": fingerprint,
                                             "bundle": hashlib.sha256(bundle.encode()).hexdigest()})}
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(f"{FOLDER}/{name}", data)
    return {"tag_name": f"v{version}", "name": version, "draft": False, "prerelease": False,
            "body": "Release notes", "published_at": "2026-10-27T00:00:00Z", "assets": [{
                "id": 123, "name": f"{FOLDER}-{version}.zip", "size": path.stat().st_size,
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{FOLDER}-{version}.zip"}]}


class SelfUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sideloader update ")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.installed = self.home / "plugins" / FOLDER
        old = self.home / "old.zip"
        package(old, "0.5.0", "old")
        with ZipFile(old) as archive:
            archive.extractall(self.installed.parent)
        self.archive = self.home / "new.zip"
        self.remote = package(self.archive, "0.6.0", "new")
        self.data = self.home / "data" / FOLDER
        self.settings = self.home / "settings" / FOLDER
        self.settings.mkdir(parents=True)
        (self.settings / "settings.json").write_text('{"bindings":{"Mine":{"repo":"owner/repo"}}}')
        (self.settings / "ownership.json").write_text('{"user":"unchanged"}')
        self.protected = {path: path.read_bytes() for path in self.settings.iterdir()}
        sdk = patch.multiple(main.decky, create=True, DECKY_HOME=str(self.home), DECKY_PLUGIN_NAME=NAME,
                             DECKY_PLUGIN_VERSION="0.5.0", DECKY_PLUGIN_DIR=str(self.installed),
                             DECKY_PLUGIN_RUNTIME_DIR=str(self.data), DECKY_PLUGIN_SETTINGS_DIR=str(self.settings), logger=Mock())
        sdk.start(); self.addCleanup(sdk.stop)
        self.plugin = main.Plugin()
        self.fetch = Mock(side_effect=lambda *_args, **_kwargs: copy.deepcopy(self.remote))
        self.plugin._query_fetch = lambda: self.fetch
        self.plugin._transport = lambda: None
        self.download = patch("sideload.self_update.download_asset", side_effect=lambda _url, path, *_args, **_kwargs: shutil.copyfile(self.archive, path))
        self.download.start(); self.addCleanup(self.download.stop)
        self.plugin._self_update_init()
        self.frontend = {"version": "0.5.0", "fingerprint": build_snapshot(self.installed)["installedFrontend"]}

    async def ready(self):
        checked = await self.plugin.check_self_update()
        release = checked["check"]["release"]
        status = await self.plugin.prepare_self_update(release["tag"], release["sha256"])
        await self.plugin._self_download_task
        status = await self.plugin.get_self_update_status(self.frontend)
        self.assertEqual(status["operation"]["stage"], "ready")
        return status["operation"]["id"]

    def test_only_fixed_stable_asset_with_digest_is_eligible(self):
        self.assertEqual(select_release(self.remote, "0.5.0")["comparison"], 1)
        self.assertEqual(select_release(self.remote, "0.6.0")["comparison"], 0)
        self.assertEqual(select_release(self.remote, "0.7.0")["comparison"], -1)
        for mutate in (lambda item: item.update(prerelease=True),
                       lambda item: item["assets"][0].update(digest=""),
                       lambda item: item["assets"][0].update(browser_download_url="https://example.org/plugin.zip"),
                       lambda item: item["assets"].append(copy.deepcopy(item["assets"][0]))):
            candidate = copy.deepcopy(self.remote)
            mutate(candidate)
            with self.assertRaises(RuntimeError):
                select_release(candidate, "0.5.0")

    async def test_stale_release_and_non_upgrade_never_start_a_download(self):
        with self.assertRaises(RuntimeError):
            await self.plugin.prepare_self_update("v0.6.0", "f" * 64)
        for current in ("0.6.0", "0.7.0"):
            self.plugin._self_current = current
            with self.assertRaises(RuntimeError):
                await self.plugin.prepare_self_update("v0.6.0", self.remote["assets"][0]["digest"][7:])
        self.assertIsNone(self.plugin._self_record["operation"])
        self.assertEqual(list(self.data.joinpath("self-update").glob("*/plugin.zip")), [])

    async def test_new_instance_and_matching_frontend_are_required_to_complete(self):
        identifier = await self.ready()
        await self.plugin.submit_self_update(identifier, self.frontend)
        with self.assertRaises(RuntimeError):
            await self.plugin.submit_self_update(identifier, self.frontend)
        with ZipFile(self.archive) as archive:
            archive.extractall(self.installed.parent)
        self.assertNotEqual((await self.plugin.get_self_update_status(self.frontend))["operation"]["stage"], "completed")
        with patch.object(main.decky, "DECKY_PLUGIN_VERSION", "0.6.0"):
            new = main.Plugin()
            status = await new.get_self_update_status()
            self.assertEqual(status["operation"]["stage"], "verifying")
            wrong = await new.get_self_update_status(self.frontend)
            self.assertEqual(wrong["operation"]["stage"], "verifying")
            frontend = {"version": "0.6.0", "fingerprint": build_snapshot(self.installed)["installedFrontend"]}
            good = await new.get_self_update_status(frontend)
            self.assertEqual(good["operation"]["stage"], "completed")
            self.assertFalse((self.data / "self-update" / identifier / "plugin.zip").exists())
        for path, data in self.protected.items():
            self.assertEqual(path.read_bytes(), data)

    async def test_matching_disk_without_matching_runtime_cannot_report_success(self):
        identifier = await self.ready()
        await self.plugin.submit_self_update(identifier, self.frontend)
        with ZipFile(self.archive) as archive:
            archive.extractall(self.installed.parent)
        (self.installed / "main.py").write_text("CORRUPT = True\n")
        with patch.object(main.decky, "DECKY_PLUGIN_VERSION", "0.6.0"):
            new = main.Plugin()
            frontend = {"version": "0.6.0", "fingerprint": build_snapshot(self.installed)["installedFrontend"]}
            self.assertNotEqual((await new.get_self_update_status(frontend))["operation"]["stage"], "completed")

    async def test_prepared_package_tamper_or_stale_frontend_blocks_handoff(self):
        identifier = await self.ready()
        with self.assertRaises(RuntimeError):
            await self.plugin.submit_self_update(identifier, {**self.frontend, "fingerprint": "f" * 64})
        (self.data / "self-update" / identifier / "plugin.zip").write_bytes(b"tampered")
        with self.assertRaises(RuntimeError):
            await self.plugin.submit_self_update(identifier, self.frontend)
        self.assertEqual(self.plugin._self_record["operation"]["stage"], "ready")
        self.assertIsNone(self.plugin._self_record["operation"]["request_id"])

    async def test_cancelled_native_request_is_bound_and_does_not_modify_program(self):
        identifier = await self.ready()
        before = (self.installed / "dist/index.js").read_bytes()
        await self.plugin.submit_self_update(identifier, self.frontend)
        digest = self.plugin._self_record["operation"]["package"]["sha256"]
        await self.plugin.record_self_update_event(identifier, "prompt", "request-1", digest)
        await self.plugin.record_self_update_event(identifier, "cancelled", "", "unrelated")
        self.assertEqual(self.plugin._self_record["operation"]["stage"], "awaiting")
        await self.plugin.record_self_update_event(identifier, "cancelled", "", "request-1")
        self.assertEqual(self.plugin._self_record["operation"]["stage"], "cancelled")
        self.assertEqual((self.installed / "dist/index.js").read_bytes(), before)

    async def test_conflicting_prompt_cannot_bind_a_later_start(self):
        identifier = await self.ready()
        await self.plugin.submit_self_update(identifier, self.frontend)
        await self.plugin.record_self_update_event(identifier, "prompt", "foreign", "f" * 64)
        await self.plugin.record_self_update_event(identifier, "start")
        self.assertEqual(self.plugin._self_record["operation"]["stage"], "unknown")
        self.assertFalse(self.plugin._self_record["operation"]["saw_start"])

    async def test_ordinary_install_and_self_update_exclude_each_other(self):
        identifier = await self.ready()
        with self.assertRaises(RuntimeError):
            await self.plugin.prepare_install("owner/repo", "v1", "https://github.com/owner/repo/releases/download/v1/plugin.zip")
        await self.plugin.cancel_self_update(identifier)
        self.plugin._flow = types.SimpleNamespace(operation={"stage": "awaiting"})
        with self.assertRaises(RuntimeError):
            await self.plugin.prepare_self_update("v0.6.0", self.remote["assets"][0]["digest"][7:])

    async def test_download_cancellation_drains_worker_before_removing_package(self):
        started, release = threading.Event(), threading.Event()
        def download(_url, path, *_args, **kwargs):
            shutil.copyfile(self.archive, path)
            started.set()
            release.wait(5)
            if kwargs["cancel"].is_set():
                raise RuntimeError("cancelled")
        with patch("sideload.self_update.download_asset", side_effect=download):
            digest = self.remote["assets"][0]["digest"][7:]
            status = await self.plugin.prepare_self_update("v0.6.0", digest)
            await asyncio.to_thread(started.wait, 5)
            identifier = status["operation"]["id"]
            task = asyncio.create_task(self.plugin.cancel_self_update(identifier))
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            result = await task
        self.assertEqual(result["operation"]["stage"], "cancelled")
        self.assertFalse((self.data / "self-update" / identifier / "plugin.zip").exists())

    async def test_unload_waits_for_check_and_prevents_a_late_write(self):
        started, release = threading.Event(), threading.Event()
        def fetch(*_args, **_kwargs):
            started.set()
            release.wait(5)
            return self.remote
        self.plugin._query_fetch = lambda: fetch
        task = asyncio.create_task(self.plugin.check_self_update())
        await asyncio.to_thread(started.wait, 5)
        stopped = asyncio.create_task(self.plugin._self_update_unload())
        await asyncio.sleep(0)
        self.assertFalse(stopped.done())
        release.set()
        await asyncio.gather(stopped, task)
        self.assertFalse(self.plugin._self_file.exists())

    async def test_corrupt_record_is_not_replaced_by_an_empty_default(self):
        self.plugin._self_root.mkdir(parents=True)
        self.plugin._self_file.write_text("{broken")
        new = main.Plugin()
        status = await new.get_self_update_status(self.frontend)
        self.assertIsNotNone(status["error"])
        with self.assertRaises(RuntimeError):
            await new.check_self_update()
        self.assertEqual(self.plugin._self_file.read_text(), "{broken")
