import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


PROVIDER = {"id": "runninghub", "base_url": "https://www.runninghub.ai"}
V2_OK = {
    "code": 0,
    "msg": "success",
    "data": {
        "type": "image",
        "download_url": "https://cdn.example/input/openapi/abc.png",
        "fileName": "openapi/abc.png",
        "size": "67",
    },
}
# Live legacy /task/openapi/upload responses for current RunningHub keys.
LEGACY_KEY_REJECTED = {"code": -1, "msg": "ApiKey verification failed", "data": None}
LEGACY_KEY_MISSING = {"code": -1, "msg": "apiKey is required", "data": None}


class RunningHubAssetUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(main, "runninghub_api_key", return_value="secret-key")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_binary_upload_is_the_primary_path(self):
        client = FakeAsyncClient([FakeResponse(V2_OK)])

        result = await main.runninghub_upload_asset_content(
            client, PROVIDER, "probe.png", b"bytes", "image/png", use_wallet=True
        )

        self.assertEqual(result["fileName"], "openapi/abc.png")
        self.assertEqual(result["downloadUrl"], "https://cdn.example/input/openapi/abc.png")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            client.calls[0]["url"], "https://www.runninghub.ai/openapi/v2/media/upload/binary"
        )
        # The legacy endpoint rejects current keys outright, so it must not be tried first.
        self.assertNotIn("apiKey", client.calls[0].get("data") or {})
        self.assertEqual(client.calls[0]["headers"]["Authorization"], "Bearer secret-key")

    async def test_legacy_endpoint_is_used_only_as_fallback(self):
        legacy_ok = {"code": 0, "msg": "success", "data": {"fileName": "api/legacy.png"}}
        client = FakeAsyncClient([FakeResponse({"code": 500, "msg": "boom"}), FakeResponse(legacy_ok)])

        result = await main.runninghub_upload_asset_content(
            client, PROVIDER, "probe.png", b"bytes", "image/png", use_wallet=False
        )

        self.assertEqual(result["fileName"], "api/legacy.png")
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(client.calls[1]["url"].endswith("/task/openapi/upload"))

    async def test_surfaces_primary_error_when_legacy_also_rejects_key(self):
        client = FakeAsyncClient([
            FakeResponse({"code": 401, "msg": "unauthorized"}, status_code=401),
            FakeResponse(LEGACY_KEY_REJECTED, status_code=401),
            FakeResponse(LEGACY_KEY_REJECTED, status_code=401),
            FakeResponse(LEGACY_KEY_MISSING, status_code=400),
        ])

        with self.assertRaises(main.HTTPException) as caught:
            await main.runninghub_upload_asset_content(
                client, PROVIDER, "probe.png", b"bytes", "image/png", use_wallet=True
            )

        # The user-facing error must describe the upload step, not leak "apiKey is required".
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("unauthorized", caught.exception.detail)

    async def test_reference_filename_helper_uses_shared_upload(self):
        client = FakeAsyncClient([FakeResponse(V2_OK)])

        with patch.object(main, "runninghub_local_asset_path", return_value=None):
            with patch.object(main, "output_file_from_url", return_value=None):
                file_name = await main.runninghub_upload_local_to_filename(
                    client, PROVIDER, "/assets/uploads/missing.png"
                )

        # Unresolvable local paths still short-circuit without an upload attempt.
        self.assertEqual(file_name, "")
        self.assertEqual(len(client.calls), 0)


if __name__ == "__main__":
    unittest.main()
