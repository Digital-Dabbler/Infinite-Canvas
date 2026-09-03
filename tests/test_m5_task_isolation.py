import asyncio
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


class CanvasTaskAtomicCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tasks_path = os.path.join(self.temp_dir.name, "canvas_tasks.json")
        self.canvas_dir = os.path.join(self.temp_dir.name, "canvases")
        os.makedirs(self.canvas_dir)
        self.patchers = [
            patch.object(main, "CANVAS_TASKS_FILE", self.tasks_path),
            patch.object(main, "CANVAS_DIR", self.canvas_dir),
        ]
        for patcher in self.patchers:
            patcher.start()
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False
        self.canvas = {
            "id": "canvas-bound", "kind": "smart", "title": "bound", "updated_at": 1,
            "nodes": [{"id": "node-bound", "type": "smart-image-generation", "images": []}],
            "logs": [], "connections": [],
        }
        main.save_canvas(self.canvas)

    def tearDown(self):
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def _task(self, task_id="canvas_img_bound"):
        return {
            "id": task_id, "type": "online-image", "status": "running",
            "provider_id": "runninghub", "model": "model-a", "created_at": time.time(), "updated_at": time.time(),
            "canvas_binding": {
                "canvas_id": "canvas-bound", "node_id": "node-bound", "batch_id": "batch-a",
                "batch_index": 0,
            },
        }

    def test_bound_completion_is_idempotent_and_updates_current_canvas_node(self):
        task = main.canvas_task_create(self._task())
        main.register_bound_canvas_task(task, {"prompt": "test", "nodeType": "smart-image-generation"}, main.now_ms())
        queued_revision = main.load_canvas("canvas-bound").get("sync_revision", 0)
        first = main.finalize_bound_canvas_task(task["id"], {"image_items": [{"url": "/output/result.png", "kind": "image"}]})
        second = main.finalize_bound_canvas_task(task["id"], {"image_items": [{"url": "/output/result.png", "kind": "image"}]})

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        saved = main.load_canvas("canvas-bound")
        node = saved["nodes"][0]
        self.assertEqual([item["url"] for item in node["images"]], ["/output/result.png"])
        self.assertEqual(node.get("pending"), 0)
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(len(saved["logs"]), 1)
        self.assertEqual(saved["logs"][0]["local_task_id"], task["id"])
        self.assertEqual(saved["logs"][0]["prompt"], "test")
        self.assertGreater(saved.get("sync_revision", 0), queued_revision)
        self.assertNotIn("log_run", main.canvas_task_get(task["id"])["canvas_binding"])

    def test_terminal_bound_broadcast_explicitly_clears_removed_task_fields(self):
        task = main.canvas_task_create(self._task("canvas_img_clear_fields"))
        main.register_bound_canvas_task(task, {"prompt": "test"}, main.now_ms())
        canvas = main.finalize_bound_canvas_task(task["id"], {"images": ["/output/result.png"]})
        async def broadcast_operation():
            with patch.object(main.manager, "broadcast_canvas_operation", new_callable=AsyncMock) as broadcast:
                await main.broadcast_bound_canvas_node(task["id"], canvas)
                return broadcast.await_args.args[1]

        operation = asyncio.run(broadcast_operation())

        self.assertIn("pendingTasks", operation["clear_fields"])
        self.assertEqual(operation["fields"]["pending"], 0)
        self.assertFalse(operation["fields"]["running"])

    def test_parallel_bound_task_broadcast_keeps_remaining_pending_tasks(self):
        first = main.canvas_task_create(self._task("canvas_img_parallel_first"))
        second = self._task("canvas_img_parallel_second")
        second["canvas_binding"]["batch_id"] = "batch-b"
        second = main.canvas_task_create(second)
        main.register_bound_canvas_task(first, {"prompt": "first"}, main.now_ms())
        main.register_bound_canvas_task(second, {"prompt": "second"}, main.now_ms())
        canvas = main.finalize_bound_canvas_task(first["id"], {"images": ["/output/first.png"]})

        async def broadcast_operation():
            with patch.object(main.manager, "broadcast_canvas_operation", new_callable=AsyncMock) as broadcast:
                await main.broadcast_bound_canvas_node(first["id"], canvas)
                return broadcast.await_args.args[1]

        operation = asyncio.run(broadcast_operation())
        remaining = operation["fields"]["pendingTasks"]

        self.assertEqual([item["taskId"] for item in remaining], [second["id"]])
        self.assertNotIn("pendingTasks", operation["clear_fields"])

    def test_bound_completion_does_not_revive_deleted_node(self):
        task = main.canvas_task_create(self._task("canvas_img_deleted"))
        main.register_bound_canvas_task(task, {"prompt": "test"}, main.now_ms())
        saved = main.load_canvas("canvas-bound")
        saved["nodes"] = []
        main.save_canvas(saved)

        self.assertIsNone(main.finalize_bound_canvas_task(task["id"], {"images": ["/output/result.png"]}))
        self.assertEqual(main.canvas_task_get(task["id"])["canvas_attach_status"], "node_missing")

    def test_bound_batch_results_keep_submission_order_when_completion_reverses(self):
        first = self._task("canvas_img_first")
        second = self._task("canvas_img_second")
        first["canvas_binding"]["batch_index"] = 0
        second["canvas_binding"]["batch_index"] = 1
        first = main.canvas_task_create(first)
        second = main.canvas_task_create(second)
        started_at = main.now_ms()
        main.register_bound_canvas_task(first, {"prompt": "test"}, started_at)
        main.register_bound_canvas_task(second, {"prompt": "test"}, started_at)

        main.finalize_bound_canvas_task(second["id"], {"images": ["/output/second.png"]})
        main.finalize_bound_canvas_task(first["id"], {"images": ["/output/first.png"]})

        saved = main.load_canvas("canvas-bound")
        self.assertEqual(
            [item["url"] for item in saved["nodes"][0]["images"]],
            ["/output/first.png", "/output/second.png"],
        )

    def test_stale_save_cannot_restore_terminal_server_task_or_replace_log(self):
        task = main.canvas_task_create(self._task("canvas_img_terminal_save"))
        main.canvas_task_update(task["id"], {"status": "succeeded"})
        incoming = [{
            "id": "node-bound",
            "pending": 1,
            "pendingTasks": [{"taskId": task["id"], "serverManaged": True}],
        }]
        cleaned = main.drop_terminal_server_managed_pending_tasks(incoming)
        self.assertNotIn("pendingTasks", cleaned[0])
        self.assertEqual(cleaned[0]["pending"], 0)
        merged = main.merge_canvas_logs(
            [{"id": "server-log", "local_task_id": task["id"], "createdAt": 20}],
            [{"id": "stale-log", "createdAt": 10}],
        )
        self.assertEqual([item["id"] for item in merged], ["server-log", "stale-log"])

    def test_reconciliation_restores_missing_log_from_terminal_task(self):
        task = main.canvas_task_create(self._task("canvas_img_reconcile"))
        main.register_bound_canvas_task(task, {"prompt": "repair me"}, main.now_ms())
        main.canvas_task_update(task["id"], {
            "status": "succeeded",
            "result": {"image_items": [{"url": "/output/reconciled.png", "kind": "image"}]},
        })

        main.reconcile_bound_canvas_tasks("canvas-bound")

        saved = main.load_canvas("canvas-bound")
        node = saved["nodes"][0]
        self.assertEqual([item["url"] for item in node["images"]], ["/output/reconciled.png"])
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(saved["logs"][0]["local_task_id"], task["id"])

    def test_node_operations_keep_latest_field_and_tombstone_wins(self):
        canvas = main.load_canvas("canvas-bound")
        first = main.CanvasOperationRequest(
            operation_id="move-one", kind="node_fields", node_id="node-bound", fields={"x": 120}
        )
        latest = main.CanvasOperationRequest(
            operation_id="move-two", kind="node_fields", node_id="node-bound", fields={"x": 360}
        )
        main.apply_canvas_node_operation(canvas, first)
        canvas = main.load_canvas("canvas-bound")
        main.apply_canvas_node_operation(canvas, latest)
        self.assertEqual(main.load_canvas("canvas-bound")["nodes"][0]["x"], 360)

        canvas = main.load_canvas("canvas-bound")
        main.apply_canvas_node_operation(canvas, main.CanvasOperationRequest(
            operation_id="delete-node", kind="node_delete", node_id="node-bound"
        ))
        with self.assertRaises(main.HTTPException) as caught:
            main.apply_canvas_node_operation(main.load_canvas("canvas-bound"), main.CanvasOperationRequest(
                operation_id="late-move", kind="node_fields", node_id="node-bound", fields={"x": 9}
            ))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(main.load_canvas("canvas-bound")["nodes"], [])

    def test_director_fields_are_validated_and_restore_uses_a_new_id(self):
        canvas = main.load_canvas("canvas-bound")
        canvas["nodes"] = [{
            "id": "director-old", "type": "smart-3d-director",
            "directorScene": {"version": 1, "entities": [], "camera": {"position": [6, 4, 7], "target": [0, 1, 0], "fov": 45}, "ratio": "16:9"},
            "directorThumb": "",
        }]
        main.save_canvas(canvas)
        main.apply_canvas_node_operation(main.load_canvas("canvas-bound"), main.CanvasOperationRequest(
            operation_id="director-delete", kind="node_delete", node_id="director-old"
        ))
        restored = {"id": "director-new", "type": "smart-3d-director", "directorScene": {"version": 1, "entities": [], "camera": {"position": [1, 2, 3], "target": [0, 1, 0], "fov": 50}, "ratio": "1:1"}, "directorThumb": ""}
        main.apply_canvas_node_operation(main.load_canvas("canvas-bound"), main.CanvasOperationRequest(
            operation_id="director-restore", kind="node_restore", node_id="director-old", node=restored
        ))
        saved = main.load_canvas("canvas-bound")
        self.assertEqual(saved["nodes"][0]["id"], "director-new")
        with self.assertRaises(main.HTTPException) as caught:
            main.apply_canvas_node_operation(saved, main.CanvasOperationRequest(
                operation_id="bad-director-thumb", kind="node_fields", node_id="director-new", fields={"directorThumb": "https://invalid.example/thumb.jpg"}
            ))
        self.assertEqual(caught.exception.status_code, 400)

    def test_server_side_integration_operation_advances_revision_without_snapshot(self):
        canvas = main.load_canvas("canvas-bound")
        revision = main.record_canvas_server_operation(canvas, "node_fields", "node-bound")

        saved = main.load_canvas("canvas-bound")
        self.assertEqual(saved["sync_revision"], revision)
        self.assertEqual(saved["operation_log"][-1]["kind"], "node_fields")
        self.assertEqual(saved["operation_log"][-1]["node_id"], "node-bound")

    def test_canvas_acl_distinguishes_viewer_editor_owner_and_admin_governance(self):
        canvas = {"owner_user_id": "owner", "editor_user_ids": ["editor"]}
        self.assertEqual(main.canvas_access_role(canvas, {"id": "viewer", "role": "user"}), "viewer")
        self.assertEqual(main.canvas_access_role(canvas, {"id": "editor", "role": "user"}), "editor")
        self.assertEqual(main.canvas_access_role(canvas, {"id": "owner", "role": "user"}), "owner")
        self.assertEqual(main.canvas_access_role(canvas, {"id": "admin", "role": "admin"}), "viewer")
        self.assertEqual(main.canvas_access_role(canvas, {"id": "admin", "role": "admin"}, governance=True), "admin")
        with self.assertRaises(main.HTTPException):
            main.require_canvas_access(canvas, {"id": "viewer", "role": "user"}, "editor")

    def test_connection_operations_merge_independently(self):
        first = {"from": "node-bound", "to": "node-a", "kind": "flow"}
        second = {"from": "node-bound", "to": "node-b", "kind": "flow"}
        for index, connection in enumerate((first, second)):
            canvas = main.load_canvas("canvas-bound")
            main.apply_canvas_node_operation(canvas, main.CanvasOperationRequest(
                operation_id=f"connection-{index}", kind="connection_add", fields={"connection": connection}
            ))
        saved = main.load_canvas("canvas-bound")
        self.assertEqual({item["to"] for item in saved["connections"]}, {"node-a", "node-b"})

    def test_setting_and_log_operations_do_not_replace_each_other(self):
        canvas = main.load_canvas("canvas-bound")
        main.apply_canvas_node_operation(canvas, main.CanvasOperationRequest(
            operation_id="setting-engine", kind="settings_fields", fields={"engine": "api"}
        ))
        canvas = main.load_canvas("canvas-bound")
        main.apply_canvas_node_operation(canvas, main.CanvasOperationRequest(
            operation_id="setting-model", kind="settings_fields", fields={"model": "model-a"}
        ))
        canvas = main.load_canvas("canvas-bound")
        main.apply_canvas_node_operation(canvas, main.CanvasOperationRequest(
            operation_id="log-one", kind="log_add", fields={"log": {"id": "log-one", "createdAt": 1}}
        ))
        saved = main.load_canvas("canvas-bound")
        self.assertEqual(saved["settings"], {"engine": "api", "model": "model-a"})
        self.assertEqual([item["id"] for item in saved["logs"]], ["log-one"])

    def test_media_catalog_operations_append_without_replacing(self):
        for index in range(2):
            canvas = main.load_canvas("canvas-bound")
            main.apply_canvas_node_operation(canvas, main.CanvasOperationRequest(
                operation_id=f"catalog-{index}", kind="media_catalog_add",
                fields={"item": {"url": f"/output/{index}.png", "kind": "image"}}
            ))
        saved = main.load_canvas("canvas-bound")
        self.assertEqual({item["url"] for item in saved["media_catalog"]}, {"/output/0.png", "/output/1.png"})


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
