import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class CanvasBoundTaskLogTests(unittest.IsolatedAsyncioTestCase):
    """A server-managed (canvas-bound) task must record a full run snapshot in
    its generation-history row and broadcast that row to live clients so the
    生成日志 panel reflects the finished run without a reload."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.canvases = self.data / "canvases"
        self.canvases.mkdir(parents=True, exist_ok=True)
        self.canvas_tasks_file = self.data / "canvas_tasks.json"
        self.patches = [
            patch.object(main, "DATA_DIR", str(self.data)),
            patch.object(main, "CANVAS_DIR", str(self.canvases)),
            patch.object(main, "CANVAS_TASKS_FILE", str(self.canvas_tasks_file)),
        ]
        for item in self.patches:
            item.start()
        # Reset the in-memory task cache so the temp CANVAS_TASKS_FILE is used.
        main.CANVAS_TASKS.clear()
        main.CANVAS_TASKS_LOADED = False

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        main.CANVAS_TASKS.clear()
        main.CANVAS_TASKS_LOADED = False
        self.temp.cleanup()

    async def test_finalize_stores_run_settings_and_broadcasts_log_add(self):
        canvas_id = "c-bound-task-log"
        node_id = "generate_abc"
        task_id = "canvas_img_bound"
        log_run = {
            "nodeId": node_id,
            "nodeType": "smart-image-generation",
            "kind": "image",
            "prompt": "a red apple",
            "settings": {
                "engine": "runninghub",
                "apiKind": "image",
                "provider_id": "runninghub",
                "model": "gpt-image-2.0/text-to-image",
                "count": 1,
            },
            "refs": [],
        }
        batch_id = "batch-1"
        main.canvas_task_create({
            "id": task_id,
            "type": "online-image",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "provider_id": "runninghub",
            "model": "gpt-image-2.0/text-to-image",
            "canvas_binding": {"canvas_id": canvas_id, "node_id": node_id, "batch_id": batch_id},
        })
        node = {
            "id": node_id,
            "type": "smart-image-generation",
            "title": "图片生成",
            "images": [],
            "activeImageIndex": 0,
            "pendingTasks": [{
                "taskId": task_id,
                "kind": "image",
                "providerId": "runninghub",
                "model": "gpt-image-2.0/text-to-image",
                "batchId": batch_id,
                "serverManaged": True,
                "logRun": log_run,
                "logStartedAt": int(time.time() * 1000),
            }],
            "pending": 1,
            "running": False,
            "runStartedAt": int(time.time() * 1000),
        }
        main.save_canvas({
            "id": canvas_id,
            "kind": "smart",
            "title": "test",
            "nodes": [node],
            "connections": [],
            "logs": [],
            "viewport": {"x": 0, "y": 0, "scale": 1},
            "updated_at": 0,
        })

        result = {"images": [{"url": "/assets/output/out.png", "kind": "image", "name": "out.png"}]}
        finalized = main.finalize_bound_canvas_task(task_id, result)
        self.assertIsNotNone(finalized)

        logs = finalized.get("logs") or []
        entry = next((item for item in logs if str(item.get("local_task_id") or "") == task_id), None)
        self.assertIsNotNone(entry, "finalize_bound_canvas_task must append a generation-history row")
        self.assertEqual(entry["prompt"], "a red apple")
        # The reproduction snapshot must be stored so an older output image can
        # be previewed/reproduced with its own run parameters.
        self.assertIn("runSettings", entry)
        self.assertEqual(entry["runSettings"]["model"], "gpt-image-2.0/text-to-image")
        self.assertTrue(any(
            str(item.get("url") or "") == "/assets/output/out.png" for item in entry.get("outputs") or []
        ))

        # The live client must receive the finished generation-history row.
        broadcast = AsyncMock()
        with patch.object(main.manager, "broadcast_canvas_operation", broadcast):
            await main.broadcast_bound_canvas_node(task_id, finalized)
        self.assertTrue(broadcast.await_count >= 1, "must broadcast the bound node update")
        log_adds = [
            call.args[1]
            for call in broadcast.await_args_list
            if call.args[1].get("kind") == "log_add"
        ]
        self.assertEqual(len(log_adds), 1, "must broadcast a log_add operation")
        self.assertEqual(log_adds[0]["fields"]["log"]["local_task_id"], task_id)
