import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        request = SimpleNamespace(
            state=SimpleNamespace(user={"id": "user-1", "api_profile_id": "profile-1"}),
            query_params={},
        )

        def fake_api_key(_provider=None, use_wallet=False, prefer_wallet=False):
            return "wallet-secret" if use_wallet else "coin-secret"

        with (
            patch.object(main, "runninghub_provider_for_request", return_value=provider),
            patch.object(main, "runninghub_api_key", side_effect=fake_api_key),
            patch.object(main, "remember_runninghub_task_scope") as remember_scope,
            patch.object(main.httpx, "AsyncClient", return_value=client),
        ):
            result = await main.runninghub_workflow_submit(
                main.RunningHubWorkflowSubmitRequest(
                    workflowId="workflow-1",
                    useWallet=True,
                ),
                request,
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
        remember_scope.assert_called_once_with(request, "task-1", provider, True)

    def test_known_task_uses_submission_profile_and_wallet_mode(self):
        request = SimpleNamespace(
            state=SimpleNamespace(user={"id": "owner-1", "role": "user", "api_profile_id": "new-profile"}),
            query_params={},
        )
        submitted_profile = {
            "id": "submitted-profile",
            "enabled": True,
            "providers": [
                {
                    "id": "runninghub",
                    "name": "RunningHub",
                    "base_url": "https://submitted.example",
                    "protocol": "runninghub",
                    "enabled": True,
                }
            ],
        }
        scopes = {
            "version": 1,
            "tasks": {
                "task-1": {
                    "user_id": "owner-1",
                    "api_profile_id": "submitted-profile",
                    "provider_id": "runninghub",
                    "credential_kind": "runninghub_wallet",
                    "metadata": {"use_wallet": True},
                }
            },
        }
        with (
            patch.object(main, "load_upstream_task_scopes", return_value=scopes),
            patch.object(main, "api_profile_by_id", return_value=submitted_profile),
        ):
            provider, use_wallet = main.runninghub_task_provider_for_request(
                request,
                "task-1",
                requested_use_wallet=False,
            )

        self.assertEqual(provider["_api_profile_id"], "submitted-profile")
        self.assertEqual(provider["base_url"], "https://submitted.example")
        self.assertTrue(use_wallet)

    def test_known_task_rejects_other_user(self):
        request = SimpleNamespace(
            state=SimpleNamespace(user={"id": "other-user", "role": "user", "api_profile_id": "other-profile"}),
            query_params={},
        )
        scopes = {
            "version": 1,
            "tasks": {
                "task-1": {
                    "user_id": "owner-1",
                    "api_profile_id": "submitted-profile",
                    "provider_id": "runninghub",
                    "credential_kind": "api_key",
                    "metadata": {"use_wallet": False},
                }
            },
        }
        with patch.object(main, "load_upstream_task_scopes", return_value=scopes):
            with self.assertRaises(main.HTTPException) as caught:
                main.runninghub_task_provider_for_request(request, "task-1")

        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
