import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main


INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "static" / "admin.html").read_text(encoding="utf-8")
ANNOUNCEMENT_JS = (ROOT / "static" / "js" / "site-announcement.js").read_text(encoding="utf-8")
STUDIO_SHELL_JS = (ROOT / "static" / "js" / "studio-shell.js").read_text(encoding="utf-8")
MAIN_SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")


class SiteAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.announcement_path = os.path.join(self.temp_dir.name, "site_announcement.json")
        self.file_patch = patch.object(main, "SITE_ANNOUNCEMENT_FILE", self.announcement_path)
        self.file_patch.start()
        self.admin_request = SimpleNamespace(
            state=SimpleNamespace(user={"id": "admin-1", "username": "admin", "role": "admin"})
        )

    def tearDown(self):
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_main_page_loads_announcement_and_sidebar_can_reopen_it(self):
        self.assertIn('src="/static/js/site-announcement.js', INDEX_HTML)
        self.assertIn('data-action="announcement"', INDEX_HTML)
        self.assertIn("window.openSiteAnnouncement?.()", STUDIO_SHELL_JS)
        self.assertNotIn('data-i18n="common.announcement"', INDEX_HTML)
        self.assertIn("window.openSiteAnnouncement = function ()", ANNOUNCEMENT_JS)
        self.assertIn("openAnnouncement(true)", ANNOUNCEMENT_JS)

    def test_api_settings_navigation_is_only_shown_to_admins(self):
        self.assertIn('id="apiSettingsEntry" data-action="api-settings" hidden', INDEX_HTML)
        self.assertIn(
            "document.getElementById('apiSettingsEntry').hidden = currentUser.role !== 'admin'",
            STUDIO_SHELL_JS,
        )
        self.assertIn("'api-settings':'/static/api-settings.html'", STUDIO_SHELL_JS)
        self.assertIn("if(initialPanel) openPanel(initialPanel,false)", STUDIO_SHELL_JS)

    def test_default_announcement_contains_requested_content(self):
        for expected in (
            "2026 年 8 月 6 日",
            "原画、UI、动效、市场",
            "已充值且可正常使用",
            "Nano Banana",
            "GPT Image 2",
            "https://www.runninghub.ai/",
            "画布资源共享规则不变",
            "其他人的画布或图片素材",
        ):
            self.assertIn(expected, MAIN_SOURCE)

    def test_refresh_suppression_is_per_version_and_one_hour(self):
        self.assertIn("const REPEAT_MS = 60 * 60 * 1000;", ANNOUNCEMENT_JS)
        self.assertIn("announcement?.id", ANNOUNCEMENT_JS)
        self.assertIn("localStorage.getItem(storageKey(announcement))", ANNOUNCEMENT_JS)
        self.assertIn("localStorage.setItem(storageKey(announcement), String(Date.now()))", ANNOUNCEMENT_JS)
        self.assertIn("elapsed >= 0 && elapsed < REPEAT_MS", ANNOUNCEMENT_JS)

    def test_announcement_uses_structured_previous_layout(self):
        for expected in (
            "site-announcement__callout",
            "site-announcement__list",
            "site-announcement__shared",
            ".split(/\\n\\s*\\n/)",
            "items.forEach",
        ):
            self.assertIn(expected, ANNOUNCEMENT_JS)
        self.assertIn("第一段显示为黄色重点区", ADMIN_HTML)
        self.assertIn("最后一段显示为绿色补充说明", ADMIN_HTML)

    def test_admin_editor_controls_content_schedule_and_enabled_state(self):
        for expected in (
            'id="announcementTitle"',
            'id="announcementContent"',
            'id="announcementStartsAt"',
            'id="announcementEndsAt"',
            'id="announcementEnabled"',
            "/api/admin/announcement",
            "立即停用",
        ):
            self.assertIn(expected, ADMIN_HTML)

    def test_admin_can_publish_and_each_save_creates_a_new_version(self):
        now = main.now_ms()
        payload = main.SiteAnnouncementRequest(
            title="测试公告",
            content="测试正文 https://example.com/",
            enabled=True,
            starts_at=now - 1000,
            ends_at=now + 3600000,
        )

        first = asyncio.run(main.admin_save_site_announcement(payload, self.admin_request))
        second = asyncio.run(main.admin_save_site_announcement(payload, self.admin_request))

        self.assertTrue(first["active"])
        self.assertTrue(second["active"])
        self.assertNotEqual(first["announcement"]["id"], second["announcement"]["id"])
        self.assertEqual(main.load_site_announcement()["updated_by"], "admin")

    def test_public_endpoint_only_returns_an_active_announcement(self):
        now = main.now_ms()
        with open(self.announcement_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "id": "active-version",
                    "title": "有效公告",
                    "content": "正文",
                    "enabled": True,
                    "starts_at": now - 1000,
                    "ends_at": now + 1000,
                },
                handle,
                ensure_ascii=False,
            )
        request = SimpleNamespace(state=SimpleNamespace(user={"id": "user-1", "role": "user"}))
        response = asyncio.run(main.site_announcement(request))
        body = json.loads(response.body)
        self.assertEqual(body["announcement"]["id"], "active-version")
        self.assertNotIn("updated_by", body["announcement"])
        self.assertEqual(response.headers["cache-control"], "no-store")

        saved = main.load_site_announcement()
        saved["enabled"] = False
        with open(self.announcement_path, "w", encoding="utf-8") as handle:
            json.dump(saved, handle, ensure_ascii=False)
        response = asyncio.run(main.site_announcement(request))
        self.assertIsNone(json.loads(response.body)["announcement"])

    def test_non_admin_cannot_update_announcement(self):
        request = SimpleNamespace(state=SimpleNamespace(user={"id": "user-1", "role": "user"}))
        now = main.now_ms()
        payload = main.SiteAnnouncementRequest(
            title="越权公告",
            content="不应保存",
            enabled=True,
            starts_at=now,
            ends_at=now + 1000,
        )
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.admin_save_site_announcement(payload, request))
        self.assertEqual(caught.exception.status_code, 403)

    def test_invalid_publish_window_is_rejected(self):
        now = main.now_ms()
        payload = main.SiteAnnouncementRequest(
            title="时间错误",
            content="正文",
            enabled=True,
            starts_at=now,
            ends_at=now,
        )
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.admin_save_site_announcement(payload, self.admin_request))
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
