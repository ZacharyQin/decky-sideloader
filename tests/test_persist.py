import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
