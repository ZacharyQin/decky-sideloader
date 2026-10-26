import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "backend"
if str(MODULES) not in sys.path:
    sys.path.insert(0, str(MODULES))

from sideload.github_meta import is_newer, parse_github_input, parse_releases, pick_zip_assets


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

    def test_download_total_belongs_to_each_release_and_includes_non_zip_assets(self) -> None:
        rows = parse_releases([
            {"tag_name": "v2", "published_at": "2026-10-25", "assets": [
                {"name": "plugin.zip", "browser_download_url": "https://x/plugin.zip", "download_count": 1234},
                {"name": "plugin.tar.gz", "download_count": 99},
                {"name": "checksums.txt", "download_count": 7},
            ], "zipball_url": "https://x/automatic-source.zip"},
            {"tag_name": "v1", "assets": [{"name": "old.zip", "download_count": 42}]},
        ], repo="plugin")
        self.assertEqual([(row["tag"], row["download_count"]) for row in rows], [("v2", 1340), ("v1", 42)])
        self.assertEqual(rows[0]["zips"], [{"name": "plugin.zip", "url": "https://x/plugin.zip"}])

    def test_missing_download_counts_are_unknown_not_zero_or_partial_totals(self) -> None:
        for assets, expected in [
            ([], 0), ([{"download_count": 0}], 0),
            (None, None), ({}, None), ([None], None), ([{}], None),
            *[([{"download_count": 100}, {"download_count": invalid}], None)
              for invalid in (None, -1, True, False, "12", 1.5)],
        ]:
            with self.subTest(assets=assets):
                row = parse_releases([{"tag_name": "v1", "assets": assets}], repo="plugin")[0]
                self.assertEqual(row["download_count"], expected)
        self.assertIsNone(parse_releases([{"tag_name": "v1"}], repo="plugin")[0]["download_count"])


    def test_embedded_version_accepts_one_semver_and_rejects_ambiguity(self) -> None:
        from sideload.github_meta import assess_checked_release, embedded_version, git_tree_version, label_version
        self.assertEqual(embedded_version("plugin-v4.5.0"), "4.5.0")
        self.assertEqual(embedded_version("dlss-enabler-4.7.4"), "4.7.4")
        self.assertIsNone(embedded_version("N03"))
        self.assertIsNone(embedded_version("1.2.3.4"))
        self.assertIsNone(embedded_version("1.0.0_2.0.0"))
        self.assertEqual(embedded_version("1.0.0-beta.2"), "1.0.0-beta.2")
        self.assertEqual(label_version("plugin-v4.5.0", ["mako.zip"]), "4.5.0")
        self.assertIsNone(label_version("v2.0.0", ["plugin-1.0.0.zip"]))
        packaged = {"tag": "plugin-v9.0.0", "has_zip": True, "zips": [{"name": "mako.zip"}]}
        self.assertEqual(assess_checked_release([packaged], "4.5.0", package_state="semver", package_version="4.5.0")["status"], "current")
        bare = {"tag": "dlss-enabler-4.7.4", "has_zip": False}
        older = {"tag": "v4.7.0", "has_zip": True, "zips": [{"name": "plugin.zip"}]}
        fallback = assess_checked_release([bare, older], "0.2.1")
        self.assertEqual(fallback["status"], "update")
        self.assertEqual(fallback["latest"], "4.7.0")
        self.assertIn("没有安装包", fallback["notice"])
        absent = assess_checked_release([packaged], "1.0.0", package_state="absent")
        self.assertEqual(absent["status"], "uncomparable")
        self.assertIn("没有可读取的版本", absent["notice"])
        import base64
        import json
        def fetch(url, **_kwargs):
            body = {"name": "MAKO"} if "/plugin.json?" in url else {"version": "4.5.0"}
            return {"encoding": "base64", "content": base64.b64encode(json.dumps(body).encode()).decode()}
        self.assertEqual(git_tree_version("acme/mako", "N03", "MAKO", fetch), "4.5.0")
        self.assertIsNone(git_tree_version("acme/mako", "N03", "Other", fetch))

if __name__ == "__main__":
    unittest.main()
