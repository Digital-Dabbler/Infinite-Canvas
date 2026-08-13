import asyncio
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, value):
        self.messages.append(value)


class M5TaskPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tasks_path = os.path.join(self.temp_dir.name, "canvas_tasks.json")
        self.path_patcher = patch.object(main, "CANVAS_TASKS_FILE", self.tasks_path)
        self.path_patcher.start()
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False

    def tearDown(self):
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False
        self.path_patcher.stop()
        self.temp_dir.cleanup()

    def test_canvas_task_survives_memory_reload_with_owner_and_profile(self):
        now = time.time()
        main.canvas_task_create({
            "id": "canvas_img_1",
            "type": "online-image",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "user_id": "user-a",
            "api_profile_id": "profile-a",
            "usage_event_id": "usage-a",
        })
        main.canvas_task_update("canvas_img_1", {"status": "succeeded", "result": {"images": ["/output/a.png"]}})

        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False

        restored = main.canvas_task_get("canvas_img_1")
        self.assertEqual(restored["status"], "succeeded")
        self.assertEqual(restored["user_id"], "user-a")
        self.assertEqual(restored["api_profile_id"], "profile-a")
        self.assertEqual(restored["result"]["images"], ["/output/a.png"])

    def test_restart_marks_local_wait_interrupted_without_resubmission(self):
        now = time.time()
        main.canvas_task_create({
            "id": "canvas_img_2",
            "type": "online-image",
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "user_id": "user-b",
            "api_profile_id": "profile-b",
            "usage_event_id": "usage-b",
            "upstream_task_id": "upstream-b",
        })

        with patch.object(main, "usage_events", return_value=[]):
            interrupted = main.recover_canvas_tasks_after_restart()

        restored = main.canvas_task_get("canvas_img_2")
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(restored["status"], "failed")
        self.assertTrue(restored["interrupted"])
        self.assertEqual(restored["upstream_task_id"], "upstream-b")
        self.assertTrue(restored["recovery_available"])
        self.assertIn("未自动重提", restored["error"])

    def test_terminal_failure_with_upstream_id_is_not_marked_recoverable(self):
        main.canvas_task_create({
            "id": "canvas_img_terminal",
            "type": "online-image",
            "status": "running",
            "created_at": time.time(),
            "updated_at": time.time(),
            "user_id": "user-a",
        })

        async def fail_terminal(*_args, **_kwargs):
            error = main.HTTPException(status_code=502, detail="上游已拒绝任务")
            error.upstream_task_id = "upstream-rejected"
            raise error

        async def run_case():
            with patch.object(main, "build_online_image_result", side_effect=fail_terminal):
                await main.run_canvas_image_task(
                    "canvas_img_terminal", SimpleNamespace(provider_id="apimart", model="gpt-image-2"), None, ""
                )

        asyncio.run(run_case())
        task = main.canvas_task_get("canvas_img_terminal")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["upstream_task_id"], "upstream-rejected")
        self.assertFalse(task["recovery_available"])


class M5TaskAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_canvas_task_is_visible_only_to_owner_or_admin(self):
        task = {
            "id": "canvas_img_auth",
            "status": "running",
            "user_id": "owner",
            "api_profile_id": "profile-owner",
        }
        owner_request = SimpleNamespace(state=SimpleNamespace(user={"id": "owner", "role": "user"}))
        other_request = SimpleNamespace(state=SimpleNamespace(user={"id": "other", "role": "user"}))
        admin_request = SimpleNamespace(state=SimpleNamespace(user={"id": "admin", "role": "admin"}))

        with patch.object(main, "canvas_task_get", return_value=task):
            self.assertEqual(
                (await main.get_canvas_image_task("canvas_img_auth", owner_request))["id"],
                "canvas_img_auth",
            )
            self.assertEqual(
                (await main.get_canvas_image_task("canvas_img_auth", admin_request))["id"],
                "canvas_img_auth",
            )
            with self.assertRaises(main.HTTPException) as caught:
                await main.get_canvas_image_task("canvas_img_auth", other_request)
        self.assertEqual(caught.exception.status_code, 403)

        with patch.object(main, "canvas_task_get", return_value=task):
            with self.assertRaises(main.HTTPException) as video_caught:
                await main.get_canvas_video_task("canvas_img_auth", other_request)
        self.assertEqual(video_caught.exception.status_code, 403)

    async def test_generation_websocket_payload_is_owner_scoped(self):
        manager = main.ConnectionManager()
        owner_ws = FakeWebSocket()
        other_ws = FakeWebSocket()
        manager.active_connections = [owner_ws, other_ws]
        manager.connection_clients = {
            owner_ws: "owner:web",
            other_ws: "other:web",
        }

        await manager.broadcast_new_image({"images": ["/output/private.png"]}, "owner")

        self.assertEqual(len(owner_ws.messages), 1)
        self.assertEqual(other_ws.messages, [])


class M5FourProfileIsolationTests(unittest.TestCase):
    def test_four_profiles_resolve_credentials_concurrently_without_crossing(self):
        profiles = {
            f"profile-{index}": {
                "id": f"profile-{index}",
                "name": f"Profile {index}",
                "enabled": True,
                "providers": [{
                    "id": "upstream",
                    "name": "Upstream",
                    "base_url": f"https://profile-{index}.invalid/v1",
                    "enabled": True,
                    "primary": True,
                }],
            }
            for index in range(4)
        }

        def resolve(index):
            return main.upstream_context_for_profile_id(
                f"profile-{index}", "upstream", user_id=f"user-{index}"
            )

        with (
            patch.object(main, "api_profile_by_id", side_effect=lambda profile_id: profiles.get(profile_id)),
            patch.object(
                main,
                "provider_env_key_value",
                side_effect=lambda _provider_id, profile_id: f"secret-{profile_id}",
            ),
            patch.object(main, "runninghub_wallet_key_value", return_value=""),
            patch.object(main, "volcengine_access_key_value", return_value=""),
            patch.object(main, "volcengine_secret_key_value", return_value=""),
        ):
            with ThreadPoolExecutor(max_workers=4) as executor:
                contexts = list(executor.map(resolve, range(4)))

        self.assertEqual(
            [context.api_key for context in contexts],
            [f"secret-profile-{index}" for index in range(4)],
        )
        self.assertEqual(
            [context.provider["base_url"] for context in contexts],
            [f"https://profile-{index}.invalid/v1" for index in range(4)],
        )

    def test_same_upstream_task_id_is_namespaced_for_four_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            scopes_path = os.path.join(directory, "upstream_task_scopes.json")
            with patch.object(main, "UPSTREAM_TASK_SCOPES_FILE", scopes_path):
                for index in range(4):
                    context = main.UpstreamContext(
                        user_id=f"user-{index}",
                        api_profile_id=f"profile-{index}",
                        api_profile_name=f"Profile {index}",
                        provider_id="upstream",
                        provider={"id": "upstream", "_api_profile_id": f"profile-{index}"},
                        api_key=f"secret-{index}",
                        runninghub_wallet_key="",
                        volcengine_access_key="",
                        volcengine_secret_key="",
                        billing_scope="department",
                    )
                    main.save_upstream_task_scope(
                        "same-task-id", context, user_id=f"user-{index}"
                    )

                stored = main.load_upstream_task_scopes()["tasks"]
                self.assertEqual(len(stored), 4)
                for index in range(4):
                    request = SimpleNamespace(
                        state=SimpleNamespace(
                            user={"id": f"user-{index}", "role": "user"}
                        )
                    )
                    scope = main.upstream_task_scope_for_request(
                        request, "same-task-id", provider_id="upstream"
                    )
                    self.assertEqual(scope["api_profile_id"], f"profile-{index}")


class M5ExtensionContractTests(unittest.TestCase):
    def test_chrome_extension_uses_revocable_bearer_identity(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "chrome-local-asset-importer"
            / "popup.js"
        ).read_text(encoding="utf-8")

        self.assertIn("'X-Client-Source': 'chrome-extension'", source)
        self.assertIn("'Authorization': `Bearer ${authToken}`", source)
        self.assertIn("/api/auth/login", source)
        self.assertIn("apiFetch('/api/providers')", source)
        self.assertIn("apiFetch('/api/local-assets/import-urls'", source)
        self.assertNotIn("password: els.password.value", source)

    def test_photoshop_clients_send_bearer_and_authenticated_websocket(self):
        root = Path(__file__).resolve().parents[1]
        connector_net = (
            root / "tools" / "photoshop-asset-connector" / "js" / "net.js"
        ).read_text(encoding="utf-8")
        connector_socket = (
            root / "tools" / "photoshop-asset-connector" / "js" / "socket.js"
        ).read_text(encoding="utf-8")
        bridge_net = (
            root
            / "tools"
            / "photoshop-canvas-bridge"
            / "client"
            / "js"
            / "net.js"
        ).read_text(encoding="utf-8")
        bridge_app = (
            root
            / "tools"
            / "photoshop-canvas-bridge"
            / "client"
            / "js"
            / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("'Authorization': `Bearer ${state.token}`", connector_net)
        self.assertIn("access_token=${encodeURIComponent(state.token)}", connector_socket)
        self.assertIn('result.Authorization = "Bearer " + state.token', bridge_net)
        self.assertIn('"&access_token=" + encodeURIComponent(net.state.token)', bridge_app)

    def test_local_cli_providers_are_explicitly_shared_local_resources(self):
        for protocol in ("jimeng", "codex", "gemini-cli"):
            provider = main.normalize_provider({
                "id": protocol,
                "name": protocol,
                "protocol": protocol,
                "billing_scope": "department",
            })
            self.assertEqual(provider["billing_scope"], "local")


if __name__ == "__main__":
    unittest.main()
