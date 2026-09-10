import asyncio
import datetime
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


def make_event(event_id, when, status="succeeded", **extra):
    event = {
        "id": event_id,
        "created_at": when.timestamp(),
        "created_at_iso": when.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": status,
        "category": "image",
        "user_id": "u1",
        "username": "one",
        "name": "用户一",
        "department": "UI",
        "department_id": "dept-ui",
        "api_profile_id": "profile-a",
        "api_profile_name": "A 组",
        "billing_scope": "department",
        "provider_id": "runninghub",
        "provider": "runninghub",
        "provider_name": "RunningHub",
        "model": "rtx-pro",
        "function": "image",
        "client_source": "web",
        "duration_ms": 1000,
    }
    event.update(extra)
    return event


class AdminUsageTimeRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_dir = os.path.join(self.temp_dir.name, "usage_audit")
        os.makedirs(self.audit_dir, exist_ok=True)
        self.policy_path = os.path.join(self.temp_dir.name, "usage_policy.json")
        with open(self.policy_path, "w", encoding="utf-8") as handle:
            json.dump({"retention_days": 180}, handle)
        self.now = datetime.datetime.now()
        self.today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.patchers = [
            patch.object(main, "USAGE_AUDIT_DIR", self.audit_dir),
            patch.object(main, "USAGE_POLICY_FILE", self.policy_path),
            patch.object(main, "USAGE_EVENTS_CACHE", {"key": None, "events": []}),
            patch.object(
                main, "require_admin", return_value={"id": "admin", "role": "admin"}
            ),
            patch.object(main, "load_canvas_tasks", return_value={}),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def request(self, **params):
        return SimpleNamespace(
            query_params={key: str(value) for key, value in params.items()}
        )

    def analytics(self, **params):
        return asyncio.run(main.admin_usage_analytics(self.request(**params)))

    def write_events(self, events):
        for event in events:
            main.append_usage_event(event)

    def at(self, day, hour, minute=0):
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def test_daily_bucket_keeps_one_point_per_day(self):
        self.write_events([
            make_event("d1", self.at(self.yesterday, 1, 15)),
            make_event("d2", self.at(self.yesterday, 9, 40)),
            make_event("d3", self.at(self.yesterday, 23, 5)),
        ])

        result = self.analytics(
            start_at=self.yesterday.isoformat(timespec="minutes"),
            end_at=(self.today).isoformat(timespec="minutes"),
            bucket_minutes=1440,
        )

        self.assertEqual(result["range"]["bucket_minutes"], 1440)
        self.assertEqual(len(result["trend"]), 1)
        self.assertEqual(
            result["trend"][0]["at"], self.yesterday.isoformat(timespec="minutes")
        )
        self.assertEqual(result["trend"][0]["succeeded"], 3)
        self.assertEqual(result["summary"]["total"], 3)

    def test_six_hour_bucket_snaps_to_day_quarters(self):
        self.write_events([
            make_event("q1", self.at(self.yesterday, 1, 15)),
            make_event("q2", self.at(self.yesterday, 7, 30)),
            make_event("q3", self.at(self.yesterday, 13, 45)),
            make_event("q4", self.at(self.yesterday, 19, 10)),
        ])

        result = self.analytics(
            start_at=self.yesterday.isoformat(timespec="minutes"),
            end_at=self.today.isoformat(timespec="minutes"),
            bucket_minutes=360,
        )

        self.assertEqual(
            [row["at"] for row in result["trend"]],
            [
                self.at(self.yesterday, hour).isoformat(timespec="minutes")
                for hour in (0, 6, 12, 18)
            ],
        )

    def test_quarter_hour_bucket_keeps_minute_alignment(self):
        self.write_events([
            make_event("m1", self.at(self.yesterday, 10, 2)),
            make_event("m2", self.at(self.yesterday, 10, 14)),
            make_event("m3", self.at(self.yesterday, 10, 16)),
        ])

        result = self.analytics(
            start_at=self.yesterday.isoformat(timespec="minutes"),
            end_at=self.today.isoformat(timespec="minutes"),
            bucket_minutes=15,
        )

        self.assertEqual(
            [(row["at"], row["succeeded"]) for row in result["trend"]],
            [
                (self.at(self.yesterday, 10, 0).isoformat(timespec="minutes"), 2),
                (self.at(self.yesterday, 10, 15).isoformat(timespec="minutes"), 1),
            ],
        )

    def test_scope_bounds_include_the_last_selected_minute(self):
        self.write_events([
            make_event("before", self.at(self.yesterday, 9, 30)),
            make_event("first", self.at(self.yesterday, 9, 31)),
            make_event("last", self.at(self.yesterday, 10, 30)),
            make_event("after", self.at(self.yesterday, 10, 31)),
            make_event("later", self.at(self.yesterday, 10, 32)),
        ])

        # 前端按 「选定结束 + 1 分钟」 发送 end_at，服务端保持 <= 比较契约。
        result = self.analytics(
            start_at=self.at(self.yesterday, 9, 31).isoformat(timespec="minutes"),
            end_at=self.at(self.yesterday, 10, 31).isoformat(timespec="minutes"),
            bucket_minutes=60,
        )

        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(
            [row["succeeded"] for row in result["trend"]],
            [1, 1],
        )

    def test_half_year_window_returns_daily_buckets(self):
        events = []
        for day_offset in range(200):
            day = self.today - datetime.timedelta(days=day_offset)
            for hour in (9, 15, 21):
                events.append(
                    make_event(f"e-{day_offset}-{hour}", self.at(day, hour))
                )
        self.write_events(events)

        window_start = self.today - datetime.timedelta(days=180)
        result = self.analytics(
            start_at=window_start.isoformat(timespec="minutes"),
            end_at=(self.today + datetime.timedelta(days=1)).isoformat(timespec="minutes"),
            bucket_minutes=1440,
        )

        expected_days = 181
        self.assertEqual(len(result["trend"]), expected_days)
        self.assertEqual(result["summary"]["total"], expected_days * 3)
        self.assertEqual(result["trend"][0]["at"], window_start.isoformat(timespec="minutes"))

    def test_usage_events_cache_reuses_parse_until_append(self):
        self.write_events([make_event("e1", self.at(self.yesterday, 12))])
        original_loads = json.loads
        parsed_rows = []

        def counting_loads(text, *args, **kwargs):
            if isinstance(text, str) and '"id":"e1"' in text:
                parsed_rows.append(text)
            return original_loads(text, *args, **kwargs)

        with patch.object(main.json, "loads", side_effect=counting_loads):
            first = main.usage_events()
            self.assertEqual(len(parsed_rows), 1)
            cached = main.usage_events()
            self.assertEqual(len(parsed_rows), 1)

            main.append_usage_event(make_event("e2", self.at(self.today, 8)))
            refreshed = main.usage_events()
            self.assertEqual(len(parsed_rows), 2)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(cached), 1)
        self.assertEqual(len(refreshed), 2)
        self.assertEqual(sorted(item["id"] for item in refreshed), ["e1", "e2"])

    def test_retention_change_is_not_served_from_cache(self):
        old_day = self.today - datetime.timedelta(days=100)
        old_month = old_day.strftime("%Y-%m")
        path = os.path.join(self.audit_dir, f"{old_month}.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(make_event("old", self.at(old_day, 12))) + "\n")
        self.write_events([make_event("new", self.at(self.yesterday, 12))])

        self.assertEqual(len(main.usage_events(180)), 2)
        self.assertEqual(len(main.usage_events(60)), 1)
        self.assertEqual(main.USAGE_EVENTS_CACHE["key"][0], 60)

    def test_export_and_usage_list_follow_the_selected_scope(self):
        recent = self.today - datetime.timedelta(days=3)
        older = self.today - datetime.timedelta(days=40)
        self.write_events([
            make_event("recent", self.at(recent, 12)),
            make_event("older", self.at(older, 12)),
        ])
        start_at = (self.today - datetime.timedelta(days=10)).isoformat(timespec="minutes")
        end_at = self.today.isoformat(timespec="minutes")

        response = asyncio.run(
            main.admin_usage_export(
                self.request(start_at=start_at, end_at=end_at)
            )
        )
        payload = json.loads(response.body)

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["filters"]["start_at"], start_at)
        self.assertEqual(payload["filters"]["end_at"], end_at)
        self.assertEqual(
            payload["events"][0]["created_at_iso"],
            self.at(recent, 12).strftime("%Y-%m-%dT%H:%M:%S"),
        )

        scoped = asyncio.run(
            main.admin_usage(self.request(start_at=start_at, end_at=end_at))
        )
        unscoped = asyncio.run(main.admin_usage(self.request()))
        self.assertEqual(scoped["total"], 1)
        self.assertEqual(unscoped["total"], 2)
