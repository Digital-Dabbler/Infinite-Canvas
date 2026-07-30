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

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class RunningHubWalletAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_wallet_bearer_body_removes_legacy_key_without_mutating_input(self):
        original = {"apiKey": "wallet-secret", "workflowId": "workflow-1"}

        clean = main.runninghub_wallet_bearer_body(original)

        self.assertEqual(clean, {"workflowId": "workflow-1"})
        self.assertEqual(original["apiKey"], "wallet-secret")

    async def test_wallet_workflow_retries_with_bearer_only_auth(self):
        verification_error = {
            "code": -1,
            "msg": "ApiKey verification failed",
            "data": None,
        }
        client = FakeAsyncClient([
            FakeResponse(verification_error),
            FakeResponse(verification_error),
            FakeResponse({"code": 0, "msg": "success", "data": {"taskId": "task-1"}}),
        ])
        provider = {"id": "runninghub", "base_url": "https://www.runninghub.cn"}

        def fake_api_key(_provider=None, use_wallet=False, prefer_wallet=False):
            return "wallet-secret" if use_wallet else "coin-secret"

        with (
            patch.object(main, "runninghub_provider", return_value=provider),
            patch.object(main, "runninghub_api_key", side_effect=fake_api_key),
            patch.object(main.httpx, "AsyncClient", return_value=client),
        ):
            result = await main.runninghub_workflow_submit(
                main.RunningHubWorkflowSubmitRequest(
                    workflowId="workflow-1",
                    useWallet=True,
                )
            )

        self.assertEqual(result["data"]["taskId"], "task-1")
        self.assertEqual(len(client.calls), 3)

        initial, body_only, bearer_only = client.calls
        self.assertEqual(initial["json"]["apiKey"], "wallet-secret")
        self.assertEqual(initial["headers"]["Authorization"], "Bearer wallet-secret")
        self.assertEqual(body_only["json"]["apiKey"], "wallet-secret")
        self.assertNotIn("Authorization", body_only["headers"])
        self.assertNotIn("apiKey", bearer_only["json"])
        self.assertEqual(bearer_only["headers"]["Authorization"], "Bearer wallet-secret")


if __name__ == "__main__":
    unittest.main()
