from __future__ import annotations

import unittest

from backend.pages import render_app_not_found, render_app_page


class AppPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = {
            "id": "org.example.app",
            "name": "Example App",
            "summary": "A useful example.",
            "version": "2.4.1",
            "version_code": 24,
            "download_url": "https://apps.example/fdroid/repo/example.apk",
            "icon_url": "https://apps.example/fdroid/repo/icons-640/example.png",
            "source_url": "https://github.com/example/app",
        }

    def test_renders_latest_download_and_fdroid_paths(self) -> None:
        page = render_app_page(
            self.app,
            repo_name="Example Repo",
            repo_url="https://apps.example/fdroid/repo",
            add_url="https://fdroid.link/#repo=https://apps.example/fdroid/repo",
            page_url="https://apps.example/apps/org.example.app",
        )

        self.assertIn("Example App", page)
        self.assertIn("org.example.app", page)
        self.assertIn("Version code</dt><dd>24", page)
        self.assertIn("Download latest APK", page)
        self.assertIn("https://apps.example/fdroid/repo/example.apk", page)
        self.assertIn("Add repository to F-Droid", page)
        self.assertIn("https://apps.example/apps/org.example.app", page)

    def test_escapes_app_metadata(self) -> None:
        app = {**self.app, "name": "<script>alert(1)</script>"}

        page = render_app_page(
            app,
            repo_name="Example Repo",
            repo_url="https://apps.example/fdroid/repo",
            add_url="https://fdroid.link/#repo=https://apps.example/fdroid/repo",
            page_url="https://apps.example/apps/org.example.app",
        )

        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_renders_not_found_page(self) -> None:
        page = render_app_not_found("org.missing.app", "Example Repo")

        self.assertIn("App not found", page)
        self.assertIn("org.missing.app", page)
        self.assertIn('href="/"', page)


if __name__ == "__main__":
    unittest.main()
