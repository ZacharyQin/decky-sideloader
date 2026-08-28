import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scan import classify_plugins, detect_github_repo, read_plugins_dir


def _write_plugin(
    root: Path,
    folder: str,
    *,
    name: str,
    author: str = "a",
    version: str | None = "1.0.0",
    package: dict | None = None,
) -> Path:
    plugin_dir = root / folder
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": name, "author": author, "flags": []}),
        encoding="utf-8",
    )
    if version is not None or package is not None:
        data = {"name": folder, "version": version or "0.0.0"}
        if package:
            data.update(package)
        (plugin_dir / "package.json").write_text(json.dumps(data), encoding="utf-8")
    return plugin_dir


class ScanTests(unittest.TestCase):
    def test_read_plugins_dir_reads_name_version_and_skips_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            _write_plugin(tmp_path, "alpha", name="Alpha", version="1.2.3")
            (tmp_path / "not-a-plugin").mkdir()
            _write_plugin(tmp_path, "no-version", name="No Version", version=None)

            plugins = read_plugins_dir(str(tmp_path))
            by_name = {p["name"]: p for p in plugins}

            self.assertEqual(set(by_name), {"Alpha", "No Version"})
            self.assertEqual(by_name["Alpha"]["folder"], "alpha")
            self.assertEqual(by_name["Alpha"]["version"], "1.2.3")
            self.assertIsNone(by_name["No Version"]["version"])

    def test_detect_github_repo(self) -> None:
        cases = [
            ({"repository": "https://github.com/acme/foo.git"}, "acme/foo"),
            ({"repository": {"type": "git", "url": "git+https://github.com/acme/bar"}}, "acme/bar"),
            ({"homepage": "https://github.com/acme/baz#readme"}, "acme/baz"),
            ({"homepage": "https://example.com/not-github"}, None),
            ({}, None),
        ]
        for package, expected in cases:
            with self.subTest(package=package):
                self.assertEqual(detect_github_repo(package), expected)

    def test_classify_marks_store_self_and_sideload(self) -> None:
        local = [
            {"name": "PowerTools", "folder": "PowerTools", "version": "1.0.0", "author": "x", "repo": None},
            {"name": "Secret Zip", "folder": "secret-zip", "version": "0.4.0", "author": "y", "repo": "y/secret"},
            {"name": "GitHub Sideload", "folder": "github-sideload", "version": "0.0.1", "author": "z", "repo": None},
        ]
        rows = classify_plugins(local, store_names={"PowerTools"}, self_name="GitHub Sideload")
        by_name = {r["name"]: r for r in rows}

        self.assertTrue(by_name["PowerTools"]["in_store"])
        self.assertFalse(by_name["PowerTools"]["sideload"])
        self.assertFalse(by_name["Secret Zip"]["in_store"])
        self.assertTrue(by_name["Secret Zip"]["sideload"])
        self.assertTrue(by_name["GitHub Sideload"]["is_self"])
        self.assertFalse(by_name["GitHub Sideload"]["sideload"])

        unknown = classify_plugins(local, store_names=set(), self_name="GitHub Sideload", store_ok=False)
        self.assertIsNone(unknown[1]["in_store"])
        self.assertFalse(unknown[1]["sideload"])


if __name__ == "__main__":
    unittest.main()
