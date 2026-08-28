import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from github_meta import is_newer, parse_github_input, parse_releases, pick_zip_assets


class GithubMetaTests(unittest.TestCase):
    def test_parse_github_input(self) -> None:
        cases = [
            ("acme/foo", "acme/foo"),
            ("acme/foo.git", "acme/foo"),
            ("https://github.com/acme/foo", "acme/foo"),
            ("https://github.com/acme/foo.git", "acme/foo"),
            ("https://github.com/acme/foo/releases/tag/v1.2.0", "acme/foo"),
            ("git+https://github.com/acme/bar", "acme/bar"),
            ("https://gitlab.com/acme/foo", None),
            ("not-a-repo", None),
            ("", None),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_github_input(raw), expected)

    def test_pick_zip_assets_prefers_plugin_name(self) -> None:
        assets = [
            {"name": "source.tar.gz", "browser_download_url": "https://x/tarball"},
            {"name": "notes.zip", "browser_download_url": "https://x/notes.zip"},
            {"name": "myplug.zip", "browser_download_url": "https://x/myplug.zip"},
        ]
        zips = pick_zip_assets(assets, repo="myplug")
        self.assertEqual([item["name"] for item in zips], ["myplug.zip", "notes.zip"])

    def test_is_newer(self) -> None:
        self.assertTrue(is_newer("1.2.0", "1.1.9"))
        self.assertFalse(is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "1.2.0"))
        self.assertIsNone(is_newer("nightly", "1.0.0"))

    def test_parse_releases_skips_drafts_and_collects_zips(self) -> None:
        payload = [
            {
                "tag_name": "v1.2.0",
                "name": "1.2.0",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {"name": "plugin.zip", "browser_download_url": "https://x/plugin.zip"},
                    {"name": "readme.md", "browser_download_url": "https://x/readme.md"},
                ],
            },
            {
                "tag_name": "v1.1.0",
                "name": "",
                "draft": True,
                "prerelease": False,
                "assets": [{"name": "plugin.zip", "browser_download_url": "https://x/old.zip"}],
            },
        ]
        releases = parse_releases(payload, repo="plugin")
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["tag"], "v1.2.0")
        self.assertEqual(releases[0]["zips"][0]["url"], "https://x/plugin.zip")


if __name__ == "__main__":
    unittest.main()
