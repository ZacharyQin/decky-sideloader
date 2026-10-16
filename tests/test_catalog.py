import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))
import catalog
from catalog import Catalog, parse_gitmodules, registration_for, validate_names

REGISTRY = '''[submodule "plugins/deck-roulette"]
 path = plugins/deck-roulette
 url = git@github.com:davocarli/deck-roulette.git
[submodule "plugins/deck-roulette"]
 path = plugins/deck-roulette
 url = https://github.com/Chrisae9/deck-roulette.git
[submodule "plugins/PowerTools"]
 path = plugins/PowerTools
 url = https://gitlab.com/NGnius/PowerTools
[submodule "plugins/decky-plugin-template"]
 path = plugins/decky-plugin-template
 url = https://github.com/SteamDeckHomebrew/decky-plugin-template
'''


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.json"
        self.now = 1000
        self.store = Mock(return_value=({"中文 插件", "Example"}, None))
        self.registry = Mock(return_value=REGISTRY)
        self.catalog = Catalog(self.path, self.store, self.registry, lambda: self.now)

    def test_live_then_fresh_cache_avoids_store_request_and_records_time(self):
        names, status, _ = self.catalog.resolve(refresh=True)
        self.assertEqual(status, {"source": "live", "timestamp": 1000, "stale": False, "warning": None})
        self.now += 10
        self.assertEqual(self.catalog.resolve()[1]["source"], "cache")
        self.store.assert_called_once()
        reloaded = Catalog(self.path, self.store, self.registry, lambda: self.now)
        self.assertEqual(reloaded.resolve()[0], names)
        self.store.assert_called_once()

    def test_expired_success_cache_is_explicit_and_failure_does_not_replace_snapshot(self):
        self.catalog.resolve(refresh=True)
        before = self.path.read_bytes()
        self.now += 301
        self.store.return_value = (set(), "timeout")
        names, status, _ = self.catalog.resolve(refresh=True)
        self.assertEqual(names, {"中文 插件", "Example"})
        self.assertEqual(status["source"], "cache")
        self.assertTrue(status["stale"])
        self.assertIn("timeout", status["warning"])
        self.assertEqual(self.path.read_bytes(), before)
        self.registry.assert_not_called()

    def test_timeout_uses_registration_not_store_membership_and_does_not_guess_aliases(self):
        self.store.return_value = (set(), "timeout")
        names, status, entries = self.catalog.resolve(refresh=True)
        self.assertEqual(names, set())
        self.assertEqual(status["source"], "registry")
        self.assertFalse(self.path.exists())
        self.assertEqual(registration_for({"name": "中文名", "folder": "deck-roulette", "repo": "Chrisae9/deck-roulette"}, entries), "registered")
        for plugin in [{"name": "deck-roulette", "folder": "中文名", "repo": "Chrisae9/deck-roulette"}, {"name": "deck-roulette", "folder": "deck-roulette", "repo": None}, {"name": "中文名", "folder": "deck-roulette", "repo": "evil/deck-roulette"}]:
            self.assertEqual(registration_for(plugin, entries), "unknown")

    def test_duplicate_sections_scp_gitlab_and_template_are_handled_without_false_names(self):
        entries = parse_gitmodules(REGISTRY)
        self.assertEqual(entries, [("deck-roulette", "chrisae9/deck-roulette"), ("deck-roulette", "davocarli/deck-roulette")])
        self.assertEqual(parse_gitmodules(REGISTRY + REGISTRY), entries)

    def test_gitmodule_fields_with_mixed_indentation_are_not_multiline_values(self):
        text = '[submodule "plugins/decky-save-manager"]\n\tpath = plugins/decky-save-manager\n  url = https://github.com/SDH-Stewardship/decky-save-manager\n'
        self.assertEqual(parse_gitmodules(text), [("decky-save-manager", "sdh-stewardship/decky-save-manager")])

    def test_damaged_catalog_and_incomplete_registry_are_not_empty_success(self):
        for payload in ([], {}, [{"name": "Valid"}, {}], [{"name": ""}], [{"name": "a\n"}], [{"name": "x"}] * 4097):
            with self.subTest(payload=str(payload)[:50]), self.assertRaises(RuntimeError):
                validate_names(payload)
        self.store.return_value = (set(), "invalid payload")
        self.registry.return_value = '[submodule "plugins/test"]\n path = plugins/test\n'
        _, status, entries = self.catalog.resolve(refresh=True)
        self.assertEqual(status["source"], "unavailable")
        self.assertIn("不完整", status["warning"])
        self.assertEqual(entries, [])
        self.assertFalse(self.path.exists())

    def test_corrupt_future_invalid_timestamp_and_oversized_cache_are_ignored(self):
        for value in ("bad JSON", json.dumps({"names": ["Valid", None], "timestamp": 999}), json.dumps({"names": ["Valid"], "timestamp": 1001}), json.dumps({"names": ["Valid"], "timestamp": -9999999})):
            self.path.write_text(value)
            loaded = Catalog(self.path, self.store, self.registry, lambda: self.now)
            self.assertIsNone(loaded.store)
        self.path.write_text(json.dumps({"names": ["Valid"], "timestamp": 999}))
        with patch.object(catalog, "MAX_CATALOG_BYTES", 1):
            self.assertIsNone(Catalog(self.path, self.store, self.registry, lambda: self.now).store)

    def test_old_success_snapshot_is_retained_without_claiming_current_membership(self):
        self.catalog.resolve(refresh=True)
        self.now += 8 * 86400
        self.store.return_value = (set(), "offline")
        names, status, _ = self.catalog.resolve(refresh=True)
        self.assertEqual(names, {"中文 插件", "Example"})
        self.assertEqual(status["source"], "cache")
        self.assertTrue(status["stale"])
        self.registry.assert_not_called()
