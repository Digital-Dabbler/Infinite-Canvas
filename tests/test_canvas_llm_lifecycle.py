import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class _Response:
    status_code = 200
    content = b'{"choices":[{"message":{"content":"generated"}}]}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "generated"}}]}


class _Client:
    timeout_values = []
    should_timeout = False

    def __init__(self, *args, **kwargs):
        self.timeout_values.append(kwargs.get("timeout"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        if self.should_timeout:
            raise httpx.ReadTimeout("upstream timed out")
        return _Response()


class CanvasLlmLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _Client.timeout_values = []
        _Client.should_timeout = False
        self.request = SimpleNamespace(state=SimpleNamespace())
        self.event = {"id": "event-1", "api_profile_id": "profile-1"}

    def patches(self):
        return (
            patch.object(main, "begin_usage_event", return_value=self.event),
            patch.object(main, "get_api_provider", return_value={"id": "provider-1"}),
            patch.object(main, "resolve_chat_provider", return_value=("https://upstream.test/v1", {}, "chat-model")),
            patch.object(main, "is_apimart_provider", return_value=False),
            patch.object(main, "finish_usage_event"),
            patch.object(main, "unwrap_apimart_response", side_effect=lambda raw: raw),
            patch.object(main.httpx, "AsyncClient", _Client),
        )

    async def test_canvas_llm_uses_dedicated_timeout(self):
        payload = main.CanvasLLMRequest(message="hello", provider="provider-1", model="chat-model")
        with self.patches()[0], self.patches()[1], self.patches()[2], self.patches()[3], self.patches()[4], self.patches()[5], self.patches()[6]:
            result = await main.canvas_llm(payload, self.request)

        self.assertEqual(result["text"], "generated")
        self.assertEqual(_Client.timeout_values, [main.LLM_REQUEST_TIMEOUT])

    async def test_canvas_llm_converts_upstream_timeout_to_readable_http_error(self):
        _Client.should_timeout = True
        payload = main.CanvasLLMRequest(message="hello", provider="provider-1", model="chat-model")
        with self.patches()[0], self.patches()[1], self.patches()[2], self.patches()[3], self.patches()[4], self.patches()[5], self.patches()[6]:
            with self.assertRaises(main.HTTPException) as caught:
                await main.canvas_llm(payload, self.request)

        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("请求上游接口失败", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
