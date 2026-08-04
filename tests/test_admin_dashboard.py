import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class AdminDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.users_path = os.path.join(self.temp_dir.name, "auth_users.json")
        with open(self.users_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "users": [
                        {
                            "id": "u1", "username": "one", "name": "用户一",
                            "department": "UI", "department_id": "dept-ui",
                            "role": "user", "enabled": True, "api_profile_id": "",
                        },
                        {
                            "id": "admin", "username": "admin", "name": "管理员",
                            "department": "管理", "role": "admin", "enabled": True,
                        },
                    ]
                },
                handle,
                ensure_ascii=False,
            )
        self.request = SimpleNamespace(
            state=SimpleNamespace(user={"id": "admin", "role": "admin"}),
            query_params={},
        )
        self.patchers = [
            patch.object(main, "AUTH_USERS_FILE", self.users_path),
            patch.object(main, "require_admin", return_value=self.request.state.user),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_bulk_profile_assignment_updates_regular_users_only(self):
        profile = {"id": "ui-budget", "name": "UI 经费", "enabled": True, "providers": []}
        with patch.object(main, "api_profile_by_id", return_value=profile):
            result = asyncio.run(
                main.admin_bulk_user_profile(
                    main.AdminBulkUserProfileRequest(
                        user_ids=["u1", "admin"], api_profile_id="ui-budget"
                    ),
                    self.request,
                )
            )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"][0]["id"], "admin")
        with open(self.users_path, "r", encoding="utf-8") as handle:
            users = json.load(handle)["users"]
        self.assertEqual(users[0]["api_profile_id"], "ui-budget")
        self.assertNotIn("api_profile_id", users[1])

    def test_usage_detail_exposes_local_output_and_detailed_error(self):
        event = {"id": "usage-1", "error": "HTTP 502", "upstream_task_id": "up-1"}
        task = {
            "id": "task-1",
            "usage_event_id": "usage-1",
            "error": "SSL: WRONG_VERSION_NUMBER",
            "result": {
                "images": [
                    "/assets/outputs/result.png",
                    "https://external.example/result.png",
                ]
            },
        }
        with patch.object(main, "load_canvas_tasks", return_value={"task-1": task}):
            detail = main.public_usage_event_detail(event)

        self.assertEqual(detail["task_id"], "task-1")
        self.assertEqual(detail["error"], "SSL: WRONG_VERSION_NUMBER")
        self.assertEqual(
            detail["outputs"], [{"url": "/assets/outputs/result.png", "kind": "image"}]
        )

    def test_runninghub_balance_parser_keeps_zero_balances(self):
        parsed = main.parse_runninghub_account_status(
            {
                "data": {
                    "remainMoney": 0,
                    "remainCoins": 12.5,
                    "currency": "USD",
                    "currentTaskCounts": 2,
                    "apiType": "enterprise",
                }
            }
        )

        self.assertEqual(parsed["balance"], 0)
        self.assertEqual(parsed["coins"], 12.5)
        self.assertEqual(parsed["currency"], "USD")
        self.assertEqual(parsed["running_tasks"], 2)

    def test_runninghub_balance_url_uses_provider_origin(self):
        self.assertEqual(
            main.runninghub_account_status_url(
                {"base_url": "https://www.runninghub.ai/openapi/v2"}
            ),
            "https://www.runninghub.ai/uc/openapi/accountStatus",
        )


if __name__ == "__main__":
    unittest.main()
