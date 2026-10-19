from __future__ import annotations

import unittest

from backend.catalog import fdroid_deep_link, parse_index_v1, parse_index_v2


class HomepageIndexTests(unittest.TestCase):
    def test_parses_standard_v1_index_and_uses_latest_version(self) -> None:
        index = {
            "apps": [
                {
                    "packageName": "org.example.app",
                    "name": "Example App",
                    "summary": "A useful example.",
                    "icon": "org.example.app.12.png",
                    "sourceCode": "https://github.com/example/app",
                }
            ],
            "packages": {
                "org.example.app": [
                    {"versionName": "1.0", "versionCode": 10, "apkName": "example-10.apk"},
                    {"versionName": "1.2", "versionCode": 12, "apkName": "example 12.apk"},
                ]
            },
        }

        apps = parse_index_v1(index, "https://apps.example/fdroid/repo")

        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["name"], "Example App")
        self.assertEqual(apps[0]["version"], "1.2")
        self.assertEqual(
            apps[0]["download_url"],
            "https://apps.example/fdroid/repo/example%2012.apk",
        )
        self.assertEqual(
            apps[0]["icon_url"],
            "https://apps.example/fdroid/repo/icons-640/org.example.app.12.png",
        )

    def test_parses_v2_localized_metadata(self) -> None:
        index = {
            "packages": {
                "org.example.second": {
                    "metadata": {
                        "name": {"en-US": "Second App"},
                        "summary": {"en-US": "A second useful app."},
                        "icon": {"en-US": {"name": "/icons-640/second.png"}},
                    },
                    "versions": {
                        "old": {
                            "manifest": {"versionName": "1.0", "versionCode": 1},
                            "file": {"name": "/second-1.apk"},
                        },
                        "new": {
                            "manifest": {"versionName": "2.0", "versionCode": 2},
                            "file": {"name": "/second-2.apk"},
                        },
                    },
                }
            }
        }

        apps = parse_index_v2(index, "https://apps.example/fdroid/repo/")

        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["name"], "Second App")
        self.assertEqual(apps[0]["version"], "2.0")
        self.assertEqual(
            apps[0]["icon_url"],
            "https://apps.example/fdroid/repo/icons-640/second.png",
        )

    def test_builds_fdroid_link_with_fingerprint(self) -> None:
        link = fdroid_deep_link(
            "https://apps.example/fdroid/repo",
            "ABC123",
        )

        self.assertEqual(
            link,
            "https://fdroid.link/#repo=https://apps.example/fdroid/repo&fingerprint=ABC123",
        )

    def test_ignores_unsafe_source_url(self) -> None:
        index = {
            "apps": [
                {
                    "packageName": "org.example.app",
                    "name": "Example App",
                    "sourceCode": "javascript:alert(1)",
                }
            ],
            "packages": {
                "org.example.app": [
                    {"versionName": "1.0", "versionCode": 1, "apkName": "example.apk"}
                ]
            },
        }

        apps = parse_index_v1(index, "https://apps.example/fdroid/repo")

        self.assertIsNone(apps[0]["source_url"])


if __name__ == "__main__":
    unittest.main()
