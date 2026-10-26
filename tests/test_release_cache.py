import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload.release_cache import ReleaseCache


class ReleaseCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.plugin = {"name": "One", "folder": "one", "repo": "acme/one", "version": "1", "ownership": {"eligible": True, "intent": {"id": "owner"}}, "skipped_tags": []}
        self.rows = [{"tag": "v2", "title": "Release Two", "body": "完整发布说明", "published_at": "2026-10-25T00:00:00Z", "repo": "acme/one", "prerelease": False, "has_zip": True, "comparison": 1, "zips": [{"name": "one.zip", "url": "https://github.com/acme/one/releases/download/v2/one.zip"}]}]
        self.stamp = "2026-10-25T00:00:00+00:00"

    def test_full_snapshot_survives_restart_but_identity_changes_cannot_reuse_it(self):
        cache = ReleaseCache(self.path)
        saved = cache.put(self.plugin, self.rows, self.stamp)
        reloaded = ReleaseCache(self.path)
        self.assertEqual(reloaded.get(self.plugin), saved)
        self.assertEqual(reloaded.get({**self.plugin, "skipped_tags": ["v2"]}), saved)
        for change in ({"folder": "other"}, {"repo": "acme/two"}, {"version": "2"}, {"ownership": {"eligible": False}}, {"ownership": {"eligible": True, "intent": {"id": "new-owner"}}}):
            self.assertIsNone(reloaded.get({**self.plugin, **change}))

    def test_corrupt_cache_is_a_miss_and_next_success_repairs_it(self):
        self.path.write_text('{"unfinished":')
        cache = ReleaseCache(self.path)
        self.assertIsNone(cache.get(self.plugin))
        saved = cache.put(self.plugin, self.rows, self.stamp)
        self.assertEqual(ReleaseCache(self.path).get(self.plugin), saved)

    def test_size_bound_evicts_oldest_without_truncating_new_release(self):
        with patch("sideload.release_cache.MAX_RELEASE_CACHE_BYTES", 1100):
            cache = ReleaseCache(self.path)
            cache.put(self.plugin, self.rows, self.stamp)
            second = {**self.plugin, "name": "Two"}
            saved = cache.put(second, self.rows, self.stamp)
            self.assertLessEqual(self.path.stat().st_size, 1100)
            reloaded = ReleaseCache(self.path)
            self.assertIsNone(reloaded.get(self.plugin))
            self.assertEqual(reloaded.get(second), saved)

    def test_unwritable_cache_keeps_success_in_memory(self):
        cache = ReleaseCache(self.path)
        with patch("sideload.release_cache.save_settings", side_effect=OSError("read-only")):
            saved = cache.put(self.plugin, self.rows, self.stamp)
        self.assertEqual(cache.get(self.plugin), saved)
