from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import apprise  # noqa: F401
except ModuleNotFoundError:
    sys.modules["apprise"] = types.ModuleType("apprise")

from backend import main


class RepositoryDeliveryTests(unittest.TestCase):
    def test_new_apks_get_distinct_stable_names_and_preserve_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "apks").mkdir()
            (root / "apks" / "videoapp-release.apk").write_bytes(b"old release")
            first = root / "first.apk"
            first.write_bytes(b"new release")
            with patch.object(main, "DATA_DIR", root):
                filename = main.store_apk(first, "videoapp-release.apk")
                self.assertEqual(
                    filename,
                    "videoapp-release-"
                    + hashlib.sha256(b"new release").hexdigest()[:16]
                    + ".apk",
                )
                self.assertEqual((root / "apks" / "videoapp-release.apk").read_bytes(), b"old release")
                self.assertEqual((root / "apks" / filename).read_bytes(), b"new release")
                again = root / "again.apk"
                again.write_bytes(b"new release")
                self.assertEqual(main.store_apk(again, "videoapp-release.apk"), filename)
                self.assertFalse(again.exists())

    def test_signed_index_certificate_supplies_deep_link_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "repo").mkdir()
            (root / "repo" / "index-v1.jar").write_bytes(b"signed index")
            certificate = "AB:" * 31 + "AB"
            with patch.object(main, "DATA_DIR", root), patch.object(main.subprocess, "run") as run:
                run.return_value.stdout = f"Signer #1:\n SHA256: {certificate}\n"
                self.assertEqual(main.repo_fingerprint(), "AB" * 32)
                self.assertEqual(main.repo_fingerprint(), "AB" * 32)
                run.assert_called_once()

    def test_static_repository_disables_cache_and_supports_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.apk").write_bytes(b"sample apk bytes")
            static = main.RepoStaticFiles(directory=root)
            scope = {
                "type": "http",
                "method": "GET",
                "headers": [(b"range", b"bytes=0-5")],
            }
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            async def request():
                response = await static.get_response("sample.apk", scope)
                await response(scope, receive, send)

            asyncio.run(request())
            self.assertEqual(sent[0]["status"], 206)
            self.assertEqual(b"".join(part.get("body", b"") for part in sent[1:]), b"sample")
            self.assertIn((b"cache-control", b"no-store"), sent[0]["headers"])

            (root / "sample-0123456789abcdef.apk").write_bytes(b"stable")
            sent.clear()

            async def immutable_request():
                response = await static.get_response("sample-0123456789abcdef.apk", scope)
                await response(scope, receive, send)

            asyncio.run(immutable_request())
            self.assertIn(
                (b"cache-control", b"public, max-age=31536000, immutable"),
                sent[0]["headers"],
            )


if __name__ == "__main__":
    unittest.main()
