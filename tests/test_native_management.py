import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload.native_management import NativeManagement
from sideload.ownership import Ownership


class NativeManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = asyncio.Queue()
        async def emit(event, request):
            await self.requests.put(request)
        self.service = NativeManagement(self.root / "native.json", emit)
        self.snapshot = {"ok": True, "plugins": [{"name": "One", "version": "1", "load_type": 1, "disabled": False}]}

    async def deliver(self, payload, refresh=False):
        task = asyncio.create_task(self.service.read(refresh))
        request = await self.requests.get()
        self.service.receive(request["id"], payload)
        return await task

    async def test_correlated_refresh_survives_reload_but_unrequested_reply_cannot_replace_it(self):
        cache = {"channel": "https://testing.deckbrew.xyz/plugins", "names": ["One"]}
        await self.deliver({"snapshot": self.snapshot, "capable": ["One"], "cache": cache}, True)
        original = self.service.path.read_bytes()
        self.service.receive("foreign", {"snapshot": self.snapshot, "capable": [], "cache": {**cache, "names": []}})
        self.assertEqual(self.service.path.read_bytes(), original)
        self.assertEqual(NativeManagement(self.service.path, None).cache, cache)
        with self.assertRaises(RuntimeError):
            await self.deliver({"snapshot": self.snapshot, "capable": None, "error": "offline"}, True)
        self.assertEqual(self.service.path.read_bytes(), original)

    async def test_late_reply_after_cancellation_or_unload_does_not_write(self):
        for cancel in (True, False):
            task = asyncio.create_task(self.service.read(True))
            request = await self.requests.get()
            if cancel:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                self.service.close()
                self.assertFalse((await task)[0]["ok"])
            self.service.receive(request["id"], {"snapshot": self.snapshot, "capable": ["One"], "cache": {"channel": "native", "names": ["One"]}})
            self.assertFalse(self.service.path.exists())

    async def test_missing_or_contradictory_native_facts_remain_unknown(self):
        for snapshot, capable in (({"ok": False}, []), (self.snapshot, ["Other"]), ({"ok": True, "plugins": self.snapshot["plugins"] * 2}, ["One"])):
            observed, result = await self.deliver({"snapshot": snapshot, "capable": capable})
            self.assertFalse(observed["ok"])
            self.assertIsNone(result)
        observed, result = await self.deliver({"snapshot": {"ok": True, "plugins": []}, "capable": []})
        self.assertTrue(observed["ok"])
        self.assertEqual(result, [])

    async def test_history_never_overrides_live_management_or_authorizes_takeover(self):
        owner = Ownership(self.root / "ownership.json")
        plugin = {"name": "One", "folder": "one", "repo": "acme/one", "repo_source": "user", "in_store": True, "cached_in_store": True, "memory_in_store": True}
        for managed, expected, eligible in ((False, "nonstore", True), (True, "store", False), (None, "unknown", False)):
            row = owner.resolve([{**plugin, "loader_managed": managed}], [])[0]
            self.assertEqual((row["ownership"]["state"], row["ownership"]["eligible"]), (expected, eligible))
            if managed is not True:
                with self.assertRaises(RuntimeError):
                    owner.begin(row, "acme/one", "takeover", [])


if __name__ == "__main__":
    unittest.main()
