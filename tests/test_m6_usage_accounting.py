import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def request_for(user=None, query=None):
    return SimpleNamespace(
        state=SimpleNamespace(user=user or {"id": "admin", "role": "admin"}),
        query_params=query or {},
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )


class M6ProfilePolicyTests(unittest.TestCase):
    def test_profile_policy_is_normalized_and_public(self):
        profile = main.normalize_api_profile({
            "id": "ui-budget",
            "name": "UI 经费",
            "usage_policy": {
                "concurrent": {"image": 3, "video": -1},
                "monthly_budget": {"image": 120},
                "budget_alert_percent": 150,
                "hard_limit_enabled": True,
            },
            "providers": [],
        })

        self.assertEqual(profile["usage_policy"]["concurrent"]["image"], 3)
        self.assertEqual(profile["usage_policy"]["concurrent"]["video"], 0)
        self.assertEqual(profile["usage_policy"]["monthly_budget"]["image"], 120)
        self.assertEqual(profile["usage_policy"]["budget_alert_percent"], 100)
        self.assertTrue(main.public_api_profile(profile)["usage_policy"]["hard_limit_enabled"])

    def test_profile_concurrency_is_isolated_by_stable_profile_id(self):
        profile_a = {
            "id": "profile-a",
            "name": "A",
            "usage_policy": {"concurrent": {"image": 1}},
        }
        profile_b = {
            "id": "profile-b",
            "name": "B",
            "usage_policy": {"concurrent": {"image": 1}},
        }
        events = [{
            "id": "event-a",
            "api_profile_id": "profile-a",
            "category": "image",
            "status": "running",
        }]

        with patch.object(main, "usage_events", return_value=events):
            with self.assertRaises(main.HTTPException) as caught:
                main.api_profile_quota_allows(profile_a, "image")
            main.api_profile_quota_allows(profile_b, "image")

        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("配置组", caught.exception.detail)

    def test_monthly_budget_only_blocks_when_hard_limit_is_enabled(self):
        event = {
            "api_profile_id": "profile-a",
            "category": "video",
            "status": "succeeded",
            "created_at_iso": main.datetime.date.today().strftime("%Y-%m") + "-01T00:00:00",
        }
        soft = {
            "id": "profile-a",
            "name": "A",
            "usage_policy": {
                "monthly_budget": {"video": 1},
                "hard_limit_enabled": False,
            },
        }
        hard = {
            **soft,
            "usage_policy": {
                "monthly_budget": {"video": 1},
                "hard_limit_enabled": True,
            },
        }

        with patch.object(main, "usage_events", return_value=[event]):
            main.api_profile_quota_allows(soft, "video")
            with self.assertRaises(main.HTTPException) as caught:
                main.api_profile_quota_allows(hard, "video")

        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("硬配额", caught.exception.detail)


class M6AuditLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_dir = os.path.join(self.temp_dir.name, "usage_audit")
        self.alerts_path = os.path.join(self.temp_dir.name, "usage_alerts.json")
        self.policy_path = os.path.join(self.temp_dir.name, "usage_policy.json")
        self.patchers = [
            patch.object(main, "USAGE_AUDIT_DIR", self.audit_dir),
            patch.object(main, "USAGE_ALERTS_FILE", self.alerts_path),
            patch.object(main, "USAGE_POLICY_FILE", self.policy_path),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_usage_event_captures_profile_and_billing_snapshots_without_secrets(self):
        user = {
            "id": "user-a",
            "username": "alice",
            "name": "Alice",
            "department": "UI",
            "department_id": "dept-ui",
            "client_source": "web",
        }
        profile = {
            "id": "profile-a",
            "name": "UI 经费",
            "enabled": True,
            "usage_policy": {},
            "providers": [{
                "id": "shared-ai",
                "name": "共享 AI",
                "enabled": True,
                "billing_scope": "shared",
            }],
        }
        request = request_for(user)

        with (
            patch.object(main, "require_authenticated", return_value=user),
            patch.object(main, "request_api_profile", return_value=profile),
        ):
            event = main.begin_usage_event(
                request,
                "image",
                "shared-ai",
                "image-model",
                {
                    "entry": "canvas",
                    "prompt": "must not persist",
                    "api_key": "secret",
                    "count": 1,
                },
            )

        self.assertEqual(event["api_profile_id"], "profile-a")
        self.assertEqual(event["api_profile_name"], "UI 经费")
        self.assertEqual(event["provider_id"], "shared-ai")
        self.assertEqual(event["provider_name"], "共享 AI")
        self.assertEqual(event["billing_scope"], "shared")
        self.assertTrue(event["shared_cost"])
        self.assertNotIn("prompt", event["params"])
        self.assertNotIn("api_key", event["params"])
        self.assertEqual(event["params"]["count"], 1)

    def test_upstream_task_id_updates_same_ledger_event(self):
        event = {
            "id": "event-a",
            "created_at": 1,
            "created_at_iso": "2026-08-01T00:00:00",
            "status": "queued",
        }
        main.append_usage_event(event)
        context = main.UpstreamContext(
            user_id="user-a",
            api_profile_id="profile-a",
            api_profile_name="A",
            provider_id="runninghub",
            provider={"id": "runninghub"},
            billing_scope="shared",
        )
        scopes_path = os.path.join(self.temp_dir.name, "upstream_task_scopes.json")
        with patch.object(main, "UPSTREAM_TASK_SCOPES_FILE", scopes_path):
            main.save_upstream_task_scope(
                "upstream-123",
                context,
                usage_event_id="event-a",
                credential_kind="runninghub_wallet",
            )
        main.finish_usage_event_by_id("event-a", "succeeded")

        rows = main.usage_events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["upstream_task_id"], "upstream-123")
        self.assertEqual(rows[0]["credential_kind"], "runninghub_wallet")
        self.assertEqual(rows[0]["status"], "succeeded")

    def test_budget_alert_contains_profile_context(self):
        profile = {
            "id": "profile-budget",
            "name": "原画经费",
            "usage_policy": {
                "monthly_budget": {"image": 1},
                "budget_alert_percent": 80,
            },
        }
        event = {
            "id": "event-budget",
            "created_at": 1,
            "created_at_iso": "2026-08-01T00:00:00",
            "api_profile_id": "profile-budget",
            "api_profile_name": "原画经费",
            "billing_scope": "department",
            "category": "image",
            "status": "queued",
        }
        main.append_usage_event(event)

        with patch.object(main, "api_profile_by_id", return_value=profile):
            main.maybe_create_profile_budget_alert(event)

        alerts = json.loads(Path(self.alerts_path).read_text(encoding="utf-8"))["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["scope"], "api_profile")
        self.assertEqual(alerts[0]["api_profile_id"], "profile-budget")
        self.assertEqual(alerts[0]["budget"], 1)

    def test_terminal_timeout_and_cancel_statuses_are_distinct(self):
        for event_id, error, expected in (
            ("timeout-event", "upstream request timed out", "timed_out"),
            ("cancel-event", "任务已取消", "cancelled"),
        ):
            event = {
                "id": event_id,
                "created_at": 1,
                "created_at_iso": "2026-08-01T00:00:00",
                "status": "queued",
            }
            main.append_usage_event(event)
            main.finish_usage_event(event, "failed", error)

        rows = {item["id"]: item for item in main.usage_events()}
        self.assertEqual(rows["timeout-event"]["status"], "timed_out")
        self.assertEqual(rows["cancel-event"]["status"], "cancelled")

    def test_fake_upstream_calls_reconcile_by_profile_provider_model_and_status(self):
        user = {
            "id": "reconcile-user",
            "username": "reconcile",
            "name": "Reconcile",
            "department": "QA",
            "department_id": "dept-qa",
        }
        profile = {
            "id": "reconcile-profile",
            "name": "对账组",
            "enabled": True,
            "usage_policy": {},
            "providers": [{
                "id": "fake-upstream",
                "name": "Fake Upstream",
                "enabled": True,
                "billing_scope": "department",
            }],
        }
        request = request_for(user)
        calls = (
            ("image", "fake-image", "succeeded", ""),
            ("video", "fake-video", "failed", "upstream rejected"),
            ("llm", "fake-llm", "failed", "任务已取消"),
        )

        with (
            patch.object(main, "require_authenticated", return_value=user),
            patch.object(main, "request_api_profile", return_value=profile),
        ):
            for function, model, status, error in calls:
                event = main.begin_usage_event(
                    request, function, "fake-upstream", model, {"entry": "fake"}
                )
                main.finish_usage_event(event, status, error)

        rows = main.usage_events()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {item["category"] for item in rows},
            {"image", "video", "llm"},
        )
        self.assertEqual(
            {item["model"] for item in rows},
            {"fake-image", "fake-video", "fake-llm"},
        )
        self.assertEqual(
            {item["status"] for item in rows},
            {"succeeded", "failed", "cancelled"},
        )
        self.assertTrue(
            all(item["api_profile_id"] == "reconcile-profile" for item in rows)
        )
        self.assertTrue(
            all(item["provider_id"] == "fake-upstream" for item in rows)
        )


class M6AdminAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_summary_matches_event_details(self):
        events = [
            {
                "id": "a",
                "created_at": 1,
                "created_at_iso": "2026-08-01T00:00:00",
                "api_profile_id": "profile-a",
                "api_profile_name": "旧名称",
                "category": "image",
                "billing_scope": "department",
                "provider": "alpha",
                "provider_name": "Alpha",
                "model": "m1",
                "status": "succeeded",
            },
            {
                "id": "b",
                "created_at": 2,
                "created_at_iso": "2026-08-01T00:01:00",
                "api_profile_id": "profile-a",
                "api_profile_name": "旧名称",
                "category": "llm",
                "billing_scope": "shared",
                "provider": "beta",
                "provider_name": "Beta",
                "model": "m2",
                "status": "failed",
            },
        ]
        profiles = {
            "version": 1,
            "profiles": [{
                "id": "profile-a",
                "name": "新名称",
                "enabled": True,
                "usage_policy": {},
                "providers": [],
            }],
        }

        with (
            patch.object(main, "usage_events", return_value=events),
            patch.object(main, "load_api_profiles", return_value=profiles),
        ):
            result = await main.admin_usage_profiles(request_for())

        row = result["profiles"][0]
        self.assertEqual(result["total"], 2)
        self.assertEqual(row["api_profile_id"], "profile-a")
        self.assertEqual(row["api_profile_name"], "旧名称")
        self.assertEqual(row["current_name"], "新名称")
        self.assertEqual(sum(row["by_category"].values()), row["total"])
        self.assertEqual(sum(row["by_billing_scope"].values()), row["total"])

    async def test_export_is_filtered_and_omits_sensitive_request_fields(self):
        events = [{
            "id": "event-a",
            "created_at": 1,
            "created_at_iso": "2026-08-01T00:00:00",
            "api_profile_id": "profile-a",
            "api_profile_name": "A",
            "category": "image",
            "billing_scope": "department",
            "status": "succeeded",
            "prompt": "private",
            "params": {"api_key": "secret"},
            "client_ip": "10.0.0.2",
            "user_agent": "private-agent",
            "raw_usage": {"total_tokens": 12, "content": "private"},
        }]
        request = request_for(query={"api_profile_id": "profile-a"})

        with patch.object(main, "usage_events", return_value=events):
            response = await main.admin_usage_export(request)

        payload = json.loads(response.body.decode("utf-8"))
        exported = payload["events"][0]
        self.assertEqual(payload["total"], 1)
        self.assertNotIn("prompt", exported)
        self.assertNotIn("params", exported)
        self.assertNotIn("client_ip", exported)
        self.assertNotIn("user_agent", exported)
        self.assertEqual(exported["raw_usage"], {"total_tokens": 12})


if __name__ == "__main__":
    unittest.main()
