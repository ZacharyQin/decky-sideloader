import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "py_modules"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))

from persist import apply_bindings, load_settings, save_settings


class PersistTests(unittest.TestCase):
    def test_apply_bindings_user_override_wins(self) -> None:
        plugins = [
            {"name": "Alpha", "repo": "detected/alpha"},
            {"name": "Beta", "repo": None},
        ]
        bindings = {"Alpha": {"repo": "user/alpha", "overridden": True}}
        rows = apply_bindings(plugins, bindings)
        self.assertEqual(rows[0]["repo"], "user/alpha")
        self.assertEqual(rows[0]["repo_source"], "user")
        self.assertEqual(rows[1]["repo"], None)
        self.assertIsNone(rows[1]["repo_source"])

    def test_old_and_unknown_network_settings_default_to_direct(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            for value in ('{}', '{"networkMode":"unknown"}', '{"networkMode":null}'):
                path.write_text(value)
                self.assertEqual(load_settings(str(path))["networkMode"], "direct")
            path.write_text('{"networkMode":"auto","bindings":{"Alpha":{"repo":"a/b"}}}')
            self.assertEqual(load_settings(str(path))["networkMode"], "auto")
            self.assertEqual(load_settings(str(path))["bindings"]["Alpha"]["repo"], "a/b")

    def test_parallel_atomic_writes_use_owned_unique_temporary_files(self):
        import json
        import stat
        import threading
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            sentinel = Path(raw) / "settings.json.tmp"
            sentinel.write_text("pre-existing file")
            barrier = threading.Barrier(8)
            def write(index):
                barrier.wait()
                save_settings(str(path), {"index": index, "value": "x" * 10000})
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(8)))
            self.assertIn(json.loads(path.read_text())["index"], range(8))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(sentinel.read_text(), "pre-existing file")
            self.assertEqual(sorted(p.name for p in Path(raw).iterdir()), ["settings.json", "settings.json.tmp"])

    def test_notification_migration_and_scheduled_report_validation(self):
        import json
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            report = {"checkedAt": "2026-10-15T00:00:00+00:00", "items": []}
            for value in (None, 0, 1, "false", {}, []):
                path.write_text(json.dumps({"checkNotifications": value, "lastCheck": report}))
                loaded = load_settings(str(path))
                self.assertTrue(loaded["checkNotifications"])
                self.assertNotIn("source", loaded["lastCheck"])
                self.assertIsNone(loaded["lastScheduledCheck"])
            for field, value in (("source", "other"), ("error", 7), ("checkedAt", "invalid"), ("items", [{}])):
                path.write_text(json.dumps({"lastScheduledCheck": {**report, field: value}}))
                self.assertIsNone(load_settings(str(path))["lastScheduledCheck"])
            path.write_text(json.dumps({"checkNotifications": False, "lastScheduledCheck": {**report, "source": "scheduled", "error": "failed"}}))
            loaded = load_settings(str(path))
            self.assertFalse(loaded["checkNotifications"])
            self.assertEqual(loaded["lastScheduledCheck"]["error"], "failed")

    def test_settings_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "settings.json"
            data = load_settings(str(path))
            self.assertEqual(data["bindings"], {})
            self.assertFalse(data["autoCheck"])
            data["autoCheck"] = True
            data["bindings"]["Alpha"] = {"repo": "a/b", "overridden": True}
            save_settings(str(path), data)
            loaded = load_settings(str(path))
            self.assertTrue(loaded["autoCheck"])
            self.assertEqual(loaded["bindings"]["Alpha"]["repo"], "a/b")


if __name__ == "__main__":
    unittest.main()
