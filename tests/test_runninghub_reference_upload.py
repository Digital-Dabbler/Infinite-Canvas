import io
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, *, content=b"", content_type="", url="", json_data=None, status_code=200):
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}
        self.url = url
        self._json_data = json_data or {}
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data


class FakeClient:
    def __init__(self, source_response):
        self.source_response = source_response
        self.get_calls = []
        self.post_calls = []

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.source_response

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse(json_data={"data": {"download_url": "https://rh.example/reference.png"}})


class RunningHubReferenceUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.RUNNINGHUB_REFERENCE_CACHE.clear()
        main.RUNNINGHUB_REFERENCE_UPLOADS.clear()

    async def test_remote_reference_is_reuploaded_instead_of_passed_through(self):
        client = FakeClient(FakeResponse(
            content=png_bytes(),
            content_type="image/png",
            url="http://127.0.0.1:3000/assets/input/reference.png",
        ))
        provider = {"id": "runninghub"}
        with (
            patch.object(main, "output_file_from_url", return_value=None),
            patch.object(main, "runninghub_openapi_url", return_value="https://rh.example/media/upload/binary"),
            patch.object(main, "runninghub_api_key", return_value="secret"),
        ):
            result = await main.runninghub_upload_reference(
                client,
                provider,
                {"url": "http://127.0.0.1:3000/assets/input/reference.png"},
            )

        self.assertEqual(result, "https://rh.example/reference.png")
        self.assertEqual(len(client.get_calls), 1)
        self.assertEqual(len(client.post_calls), 1)
        uploaded = client.post_calls[0][1]["files"]["file"]
        self.assertEqual(uploaded[1], png_bytes())
        self.assertEqual(uploaded[2], "image/png")

    async def test_same_image_reuses_cached_runninghub_url(self):
        client = FakeClient(FakeResponse(
            content=png_bytes(),
            content_type="image/png",
            url="https://source.example/reference.png",
        ))
        provider = {"id": "runninghub"}
        patches = (
            patch.object(main, "output_file_from_url", return_value=None),
            patch.object(main, "runninghub_openapi_url", return_value="https://rh.example/media/upload/binary"),
            patch.object(main, "runninghub_api_key", return_value="secret"),
        )
        with patches[0], patches[1], patches[2]:
            first = await main.runninghub_upload_reference(client, provider, {"url": "https://source.example/reference.png"})
            second = await main.runninghub_upload_reference(client, provider, {"url": "https://source.example/reference.png"})
            for key, item in main.RUNNINGHUB_REFERENCE_CACHE.items():
                if ":source:" in key:
                    item["expires_at"] = time.monotonic() - 1
            third = await main.runninghub_upload_reference(client, provider, {"url": "https://source.example/reference.png"})

        self.assertEqual(first, second)
        self.assertEqual(second, third)
        # The expired remote-URL shortcut is downloaded again to verify its
        # current bytes, but identical content is not re-uploaded to RunningHub.
        self.assertEqual(len(client.get_calls), 2)
        self.assertEqual(len(client.post_calls), 1)
        remaining = [
            item["expires_at"] - time.monotonic()
            for key, item in main.RUNNINGHUB_REFERENCE_CACHE.items()
            if ":source:" in key
        ]
        self.assertEqual(len(remaining), 1)
        self.assertGreater(remaining[0], 590)
        self.assertLessEqual(remaining[0], 600)
        content_entries = [
            item for key, item in main.RUNNINGHUB_REFERENCE_CACHE.items()
            if ":source:" not in key
        ]
        self.assertEqual(len(content_entries), 1)
        self.assertEqual(content_entries[0]["expires_at"], 0.0)

    def test_only_explicit_asset_expiry_requests_reupload(self):
        expired = FakeResponse(status_code=400)
        self.assertTrue(main.runninghub_reference_reupload_needed(
            expired,
            {"code": 400, "message": "reference image URL expired"},
        ))
        self.assertFalse(main.runninghub_reference_reupload_needed(
            expired,
            {"code": 400, "message": "prompt is invalid"},
        ))
        self.assertFalse(main.runninghub_reference_reupload_needed(
            FakeResponse(status_code=200),
            {"code": 0, "message": "ok"},
        ))

    async def test_non_image_remote_reference_stops_generation(self):
        client = FakeClient(FakeResponse(
            content=b"<html>login</html>",
            content_type="text/html",
            url="http://127.0.0.1:3000/static/login.html",
        ))
        with patch.object(main, "output_file_from_url", return_value=None):
            with self.assertRaises(main.HTTPException) as raised:
                await main.runninghub_upload_reference(
                    client,
                    {"id": "runninghub"},
                    {"url": "http://127.0.0.1:3000/assets/input/reference.png"},
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("不是有效图片", raised.exception.detail)
        self.assertEqual(client.post_calls, [])


if __name__ == "__main__":
    unittest.main()
