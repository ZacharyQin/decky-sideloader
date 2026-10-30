"""Run the frozen 0.5.0 suite as a migration oracle, separate from the Rust runtime."""
import importlib
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests/reference_backend"
sys.path.insert(0, str(REFERENCE))
sys.modules.setdefault("decky", types.ModuleType("decky"))
# Preload the reference namespace so legacy fixtures cannot import the live adapter.
importlib.import_module("main")
for name in ("repository_map", "settings_api"):
    importlib.import_module(f"sideload.{name}").BUNDLED_MAP = ROOT / "data/repositories.json"

# Frozen test modules are explicit: new live tests must never enter the legacy oracle.
REFERENCE_TESTS = (
    'test_backups.py', 'test_catalog.py', 'test_device_info.py', 'test_diagnostic_evidence.py',
    'test_diagnostics.py', 'test_diagnostics_sharing.py', 'test_download.py', 'test_github.py',
    'test_http.py', 'test_install_flow.py', 'test_install_ops.py', 'test_local_catalog.py',
    'test_management.py', 'test_native_management.py', 'test_ownership.py', 'test_persist.py',
    'test_release_cache.py', 'test_remote_binaries.py', 'test_repo_probe.py', 'test_repository_map.py',
    'test_scan.py', 'test_self_update.py', 'test_transport.py', 'test_zip_version.py',
)
suite = unittest.TestSuite()
loader = unittest.TestLoader()
for filename in REFERENCE_TESTS:
    if not (ROOT / 'tests' / filename).is_file():
        raise SystemExit('Missing frozen reference test: ' + filename)
    suite.addTests(loader.discover(str(ROOT / 'tests'), pattern=filename))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
