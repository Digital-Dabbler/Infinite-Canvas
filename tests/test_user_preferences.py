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


def auth_request(user):
    return SimpleNamespace(state=SimpleNamespace(user=user))


def write_users_file(path, users):
    path.write_text(json.dumps({"users": users}, ensure_ascii=False), encoding="utf-8")


class UserPreferencesTests(unittest.TestCase):
    """个性化偏好（画布字体缩放等）按用户隔离存储，仅本人可读写。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.users_path = os.path.join(self.temp_dir.name, "auth_users.json")
        self.user_a = {"id": "user-a", "username": "alice", "name": "Alice", "role": "user", "enabled": True}
        self.user_b = {"id": "user-b", "username": "bob", "name": "Bob", "role": "user", "enabled": True}
        write_users_file(Path(self.users_path), [self.user_a, self.user_b])
        self.users_patch = patch.object(main, "AUTH_USERS_FILE", self.users_path)
        self.users_patch.start()

    def tearDown(self):
        self.users_patch.stop()
        self.temp_dir.cleanup()

    def test_saves_font_scale_for_current_user_only(self):
        payload = main.AuthPreferencesRequest(canvas_font_scale=1.15)
        result = asyncio.run(main.auth_update_preferences(payload, auth_request(self.user_a)))
        self.assertTrue(result["ok"])
        self.assertEqual(result["preferences"]["canvas_font_scale"], 1.15)

        users = json.loads(Path(self.users_path).read_text(encoding="utf-8"))["users"]
        a = next(u for u in users if u["id"] == "user-a")
        b = next(u for u in users if u["id"] == "user-b")
        self.assertEqual(a["preferences"]["canvas_font_scale"], 1.15)
        self.assertNotIn("preferences", b)

    def test_public_user_exposes_preferences_to_shell(self):
        self.user_a["preferences"] = {"canvas_font_scale": 1.2}
        public = main.public_user(self.user_a)
        self.assertEqual(public["preferences"]["canvas_font_scale"], 1.2)

    def test_rejects_out_of_range_scale(self):
        payload = main.AuthPreferencesRequest(canvas_font_scale=2.0)
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.auth_update_preferences(payload, auth_request(self.user_a)))
        self.assertEqual(caught.exception.status_code, 400)

    def test_null_removes_preference(self):
        self.user_a["preferences"] = {"canvas_font_scale": 1.1}
        write_users_file(Path(self.users_path), [self.user_a, self.user_b])
        payload = main.AuthPreferencesRequest(canvas_font_scale=None)
        result = asyncio.run(main.auth_update_preferences(payload, auth_request(self.user_a)))
        self.assertNotIn("canvas_font_scale", result["preferences"])

    def test_unknown_user_returns_404(self):
        payload = main.AuthPreferencesRequest(canvas_font_scale=1.1)
        ghost = {"id": "ghost", "username": "ghost", "role": "user", "enabled": True}
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.auth_update_preferences(payload, auth_request(ghost)))
        self.assertEqual(caught.exception.status_code, 404)

    def test_shell_and_canvas_contain_font_scale_wiring(self):
        index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        shell_js = (ROOT / "static" / "js" / "studio-shell.js").read_text(encoding="utf-8")
        canvas_js = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('id="accountFontScale"', index_html)
        self.assertIn("studio-font-scale", shell_js)
        self.assertIn("studio-font-scale", canvas_js)
        self.assertIn("--canvas-font-scale", canvas_js)
        self.assertIn("/api/auth/me/preferences", main_source)

    def test_preferences_route_is_registered(self):
        routes = {getattr(route, "path", None) for route in main.app.routes}
        self.assertIn("/api/auth/me/preferences", routes)


if __name__ == "__main__":
    unittest.main()
