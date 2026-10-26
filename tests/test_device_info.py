import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.modules.setdefault("decky", types.ModuleType("decky"))
import main
from sideload import device_info
from sideload.diagnostics import Diagnostics
from sideload.persist import save_settings


class DeviceExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.release = self.root / "os-release"
        self.release.write_text('ID=steamos\nNAME="SteamOS"\nVERSION_ID=3.8.28\nBUILD_ID=20260922.1\nVARIANT_ID=steamdeck\nHOSTNAME=SYNTHETIC_PRIVATE_HOST\n')
        self.dmi = self.root / "dmi"
        self.dmi.mkdir()
        for name, value in {"sys_vendor": "Valve", "product_name": "Galileo", "board_name": "Galileo", "product_version": "1", "product_serial": "SYNTHETIC_SERIAL", "product_uuid": "SYNTHETIC_UUID"}.items():
            (self.dmi / name).write_text(value + "\n")
        self.home = self.root / "home"
        self.home.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.settings = self.root / "settings"
        save_settings(str(self.settings / "settings.json"), {"networkMode": "auto"})
        for context in (
            patch.object(device_info, "OS_RELEASE_PATHS", (self.release,)),
            patch.object(device_info, "DMI_DIRECTORY", self.dmi),
            patch.object(device_info.sys, "platform", "linux"),
            patch.object(device_info.os, "uname", return_value=types.SimpleNamespace(machine="x86_64", release="6.17.3-valve1-1-neptune-617", nodename="SYNTHETIC_PRIVATE_HOST")),
            patch.multiple(main.decky, create=True, DECKY_PLUGIN_SETTINGS_DIR=str(self.settings), DECKY_PLUGIN_RUNTIME_DIR=str(self.runtime), DECKY_PLUGIN_DIR=str(self.root / "not-installed")),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.service = Diagnostics(str(self.root / "logs"), str(self.home))
        self.plugin = main.Plugin()
        self.plugin._diagnostics = self.service

    async def asyncTearDown(self):
        await self.service.close()

    async def export(self, steam_version=None):
        result = await self.plugin.export_diagnostics(steam_version)
        self.assertEqual(result["state"], "success")
        with zipfile.ZipFile(result["path"]) as archive:
            summary = json.loads(archive.read("summary.json"))
            logs = b"".join(archive.read(name) for name in archive.namelist() if name.endswith(".jsonl"))
            payload = b"".join(archive.read(name) for name in archive.namelist())
        return summary["details"]["environment"], logs, payload

    async def test_only_real_exports_read_metadata_and_each_export_gets_current_values(self):
        with patch.object(device_info, "_read_text", side_effect=AssertionError("device reads outside export")):
            self.service.record("startup")
            await self.plugin.get_diagnostics_status()
            await self.plugin.get_diagnostics_share()
            await self.plugin.get_app_settings()
            self.service.share_phase = "active"
            with self.assertRaises(RuntimeError):
                await self.plugin.export_diagnostics(1792618285)
            self.service.share_phase = None
            with self.assertRaises(RuntimeError):
                await self.plugin.start_diagnostics_share("127.0.0.1", "a" * 32)
        environment, logs, payload = await self.export(1792618285)
        self.assertEqual(environment["steamClientVersion"], 1792618285)
        self.assertEqual(environment["osVersion"], "3.8.28")
        self.assertEqual(environment["osBuild"], "20260922.1")
        self.assertEqual(environment["kernelVersion"], "6.17.3-valve1-1-neptune-617")
        self.assertTrue(environment["isSteamDeck"])
        self.assertEqual(environment["steamDeckModel"], "oled")
        self.assertNotIn(b"steamClientVersion", logs)
        self.assertNotIn(b"Galileo", logs)
        self.assertNotIn(b"osVersion", logs)
        for secret in (b"SYNTHETIC_PRIVATE_HOST", b"SYNTHETIC_SERIAL", b"SYNTHETIC_UUID"):
            self.assertNotIn(secret, payload)
        self.release.write_text("ID=steamos\nVERSION_ID=3.9.0\n")
        updated, _, _ = await self.export(1792619000)
        self.assertEqual(updated["osVersion"], "3.9.0")
        self.assertEqual(updated["steamClientVersion"], 1792619000)

    async def test_hardware_identification_does_not_infer_a_deck_from_steamos(self):
        for vendor, model, expected, display in (("Valve", "Jupiter", True, "lcd"), ("ASUSTeK COMPUTER INC.", "ROG Ally", False, None), ("Valve", "Unrecognized", None, None), ("", "Galileo", None, None)):
            with self.subTest(vendor=vendor, model=model):
                (self.dmi / "sys_vendor").write_text(vendor)
                (self.dmi / "product_name").write_text(model)
                environment, _, _ = await self.export()
                self.assertIs(environment["isSteamDeck"], expected)
                self.assertEqual(environment.get("steamDeckModel"), display)

    async def test_missing_oversized_and_linked_metadata_do_not_block_export_or_leak(self):
        self.release.write_bytes(b"X" * 8193)
        product = self.dmi / "product_name"
        product.unlink()
        product.symlink_to(self.dmi / "product_serial")
        (self.dmi / "board_name").write_bytes(b"\xff")
        environment, _, payload = await self.export()
        self.assertIsNone(environment["isSteamDeck"])
        for key in ("osVersion", "deviceModel", "boardName", "steamClientVersion"):
            self.assertNotIn(key, environment)
        self.assertNotIn(b"SYNTHETIC_SERIAL", payload)
        self.assertNotIn(b"SYNTHETIC_PRIVATE_HOST", payload)

    async def test_os_release_symlink_uses_standard_usr_lib_source(self):
        fallback = self.root / "usr-lib-os-release"
        fallback.write_text('ID=ubuntu\nNAME="Ubuntu"\nVERSION_ID="24.04"\n')
        self.release.unlink()
        self.release.symlink_to(fallback)
        with patch.object(device_info, "OS_RELEASE_PATHS", (self.release, fallback)):
            environment, _, _ = await self.export()
        self.assertEqual(environment["osId"], "ubuntu")
        self.assertEqual(environment["osVersion"], "24.04")

    async def test_invalid_native_steam_versions_are_omitted_without_failing_export(self):
        for value in (True, -1, 2**53, "SYNTHETIC_TOKEN"):
            with self.subTest(value=value):
                environment, _, payload = await self.export(value)
                self.assertNotIn("steamClientVersion", environment)
                self.assertNotIn(b"SYNTHETIC_TOKEN", payload)
