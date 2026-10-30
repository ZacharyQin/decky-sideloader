"""Capture deterministic pure-function contracts from the frozen 0.5.0 test oracle."""
import base64
import functools
import importlib
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/reference_backend"))
sys.modules.setdefault("decky", types.ModuleType("decky"))
CASES = {}
COUNTS = {}


def bounded(value, budget=65536, depth=0):
    if depth > 10:
        return False
    if isinstance(value, (str, bytes)):
        return len(value) <= budget
    if value is None or type(value) in (int, float, bool):
        return True
    if isinstance(value, dict):
        return len(value) <= 128 and all(bounded(k, budget // max(1, len(value)), depth + 1) and bounded(v, budget // max(1, len(value)), depth + 1) for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return len(value) <= 128 and all(bounded(v, budget // max(1, len(value)), depth + 1) for v in value)
    return False


def encode(value):
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode()}
    if isinstance(value, set):
        return sorted(value)
    raise TypeError()


def capture(module_name, name):
    module = importlib.import_module("sideload." + module_name)
    original = getattr(module, name)
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        function = module_name + "." + name
        if COUNTS.get(function, 0) >= 400 or not bounded((args, kwargs)):
            return original(*args, **kwargs)
        try:
            inputs = json.loads(json.dumps({"args": args, "kwargs": kwargs}, default=encode, allow_nan=False))
        except (TypeError, ValueError):
            inputs = None
        try:
            result = original(*args, **kwargs)
        except Exception:
            record = {"ok": False}
            raise
        else:
            try:
                record = {"ok": True, "value": json.loads(json.dumps(result, default=encode, allow_nan=False))}
            except (TypeError, ValueError):
                record = None
            return result
        finally:
            if inputs is not None and record is not None and bounded(record):
                case = {"function": function, **inputs, **record}
                key = json.dumps(case, ensure_ascii=False, sort_keys=True)
                if str(ROOT) not in key and '"/tmp/' not in key and key not in CASES:
                    CASES[key] = case
                    COUNTS[function] = COUNTS.get(function, 0) + 1
    setattr(module, name, wrapped)


for module, functions in {
    "github_meta": ("parse_github_input", "compare_versions", "embedded_version", "label_version", "pick_zip_assets", "assess_checked_release", "parse_releases"),
    "scan": ("detect_github_repo",),
    "persist": ("apply_bindings", "skipped_tags"),
    "catalog": ("parse_gitmodules",),
    "repository_map": ("decode_mapping", "resolve_repository"),
    "release_cache": ("valid_snapshot", "release_identity", "matches"),
    "diagnostics": ("sanitize",),
    "diagnostic_snapshot": ("sanitize_snapshot",),
}.items():
    for function in functions:
        capture(module, function)
importlib.import_module("main")
for name in ("repository_map", "settings_api"):
    importlib.import_module("sideload." + name).BUNDLED_MAP = ROOT / "data/repositories.json"
suite = unittest.TestSuite()
loader = unittest.TestLoader()
for path in sorted((ROOT / "tests").glob("test_*.py")):
    if path.name != "test_bridge.py":
        suite.addTests(loader.discover(str(ROOT / "tests"), pattern=path.name))
result = unittest.TextTestRunner(verbosity=0).run(suite)
if not result.wasSuccessful():
    raise SystemExit("Reference suite failed; no fixtures published")
destination = ROOT / "backend-rs/tests/fixtures/python-050-contracts.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps({"reference": "88e3d54138d04bf1846f37f94bddda062d603874", "cases": list(CASES.values())}, ensure_ascii=False, indent=2) + "\n")
print(f"Captured {len(CASES)} pure behavior contracts from {result.testsRun} reference tests")
