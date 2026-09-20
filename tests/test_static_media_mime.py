"""Static mounts must serve images with an image Content-Type.

The bundled CPython resolves media types from the Windows registry, and a
machine without a registered Content Type for these extensions gets no entry.
Starlette's FileResponse then falls back to "text/plain", so /static, /assets
and /output all answered `.webp` covers with a type no browser renders as an
image. This is why the fault looked machine-dependent.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from starlette.applications import Starlette
from starlette.responses import guess_type
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Importing the application is what registers the media types.
import main  # noqa: E402  (import order is the point of this test)


class StaticMediaTypeTests(unittest.TestCase):
    def test_registered_types_resolve_to_images(self):
        for name, expected in (
            ("cover.webp", "image/webp"),
            ("cover.avif", "image/avif"),
        ):
            with self.subTest(name=name):
                self.assertEqual(guess_type(name)[0], expected)

    def test_static_mount_serves_webp_as_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cover.webp")
            Image.new("RGB", (8, 8), (10, 20, 30)).save(path, "WEBP")
            app = Starlette()
            app.mount("/static", StaticFiles(directory=tmp), name="static")

            response = TestClient(app).get("/static/cover.webp")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/webp")

    def test_main_registers_media_types_before_mounting_directories(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('(".webp", "image/webp")', source)
        # Registration has to happen before the mounts exist, otherwise a request
        # served in between would still fall back to text/plain.
        self.assertLess(source.index("mimetypes.add_type"), source.index('app.mount("/static"'))


if __name__ == "__main__":
    unittest.main()
