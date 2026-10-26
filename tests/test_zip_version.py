import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload.zip_version import ZipUnreadable, package_version_from_zip


def archive(files, compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as package:
        for name, content in files.items():
            package.writestr(name, content)
    return buffer.getvalue()


def fetch_of(data):
    def fetch(start, end):
        if start is None:
            chunk = data if end >= len(data) else data[-end:]
            return chunk, len(data) - len(chunk), len(data)
        return data[start:end + 1], start, len(data)
    return fetch


class ZipVersionTests(unittest.TestCase):
    def test_reads_stored_and_deflated_package_versions_without_the_whole_archive_policy(self):
        files = {"example/package.json": json.dumps({"name": "example", "version": "4.5.0"}), "example/dist/index.js": "x" * 80000}
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                self.assertEqual(package_version_from_zip(fetch_of(archive(files, compression))), ("semver", "4.5.0"))

    def test_ignores_nested_manifests_and_rejects_missing_or_ambiguous_packages(self):
        nested = archive({
            "example/package.json": json.dumps({"version": "1.2.3"}),
            "example/node_modules/left/package.json": json.dumps({"version": "9.9.9"}),
        })
        self.assertEqual(package_version_from_zip(fetch_of(nested)), ("semver", "1.2.3"))
        missing = archive({"example/plugin.json": json.dumps({"name": "Example"})})
        self.assertEqual(package_version_from_zip(fetch_of(missing)), ("absent", None))
        ambiguous = archive({
            "one/package.json": json.dumps({"version": "1.0.0"}),
            "two/package.json": json.dumps({"version": "2.0.0"}),
        })
        self.assertEqual(package_version_from_zip(fetch_of(ambiguous)), ("absent", None))
        raw = archive({"example/package.json": json.dumps({"version": "N03"})})
        self.assertEqual(package_version_from_zip(fetch_of(raw)), ("raw", "N03"))
        with self.assertRaises(ZipUnreadable):
            package_version_from_zip(fetch_of(b"not a zip, just bytes of some length!!"))


if __name__ == "__main__":
    unittest.main()
