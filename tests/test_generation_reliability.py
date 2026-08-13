import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class RunningHubSchemaContractTests(unittest.TestCase):
    def test_seedance_conversion_slots_default_is_a_list(self):
        field = {
            "fieldKey": "conversionSlots",
            "type": "STRING",
            "defaultValue": "all",
        }
        body = {}

        main.runninghub_apply_schema_defaults(body, [field])

        self.assertEqual(body["conversionSlots"], ["all"])

    def test_list_accepts_json_or_comma_separated_values(self):
        field = {"fieldKey": "slots", "type": "ARRAY"}

        self.assertEqual(
            main.runninghub_schema_normalize_value(field, '["first", "last"]'),
            ["first", "last"],
        )
        self.assertEqual(
            main.runninghub_schema_normalize_value(field, "first,\nlast"),
            ["first", "last"],
        )

    def test_object_rejects_non_json(self):
        with self.assertRaises(main.HTTPException) as caught:
            main.runninghub_schema_normalize_value(
                {"fieldKey": "options", "type": "OBJECT"}, "not json"
            )
        self.assertEqual(caught.exception.status_code, 400)

    def test_app_node_info_uses_saved_schema(self):
        fields = [{
            "nodeId": "1",
            "fieldName": "conversionSlots",
            "fieldType": "STRING",
            "defaultValue": "all",
        }]
        payload = [{"nodeId": "1", "fieldName": "conversionSlots", "fieldValue": "all"}]

        clean = main.sanitize_runninghub_node_info_list(payload, fields)

        self.assertEqual(clean[0]["fieldValue"], ["all"])


class CanvasLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tasks_path = os.path.join(self.temp_dir.name, "canvas_tasks.json")
        self.path_patcher = patch.object(main, "CANVAS_TASKS_FILE", self.tasks_path)
        self.path_patcher.start()
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False

    async def asyncTearDown(self):
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
            main.CANVAS_TASKS_LOADED = False
        self.path_patcher.stop()
        self.temp_dir.cleanup()

    async def test_video_submission_failure_has_no_recovery_state(self):
        task_id = "canvas_video_submission_failure"
        main.canvas_task_create({"id": task_id, "status": "queued", "phase": "validating", "created_at": time.time()})
        error = main.HTTPException(status_code=400, detail="payload invalid")
        payload = main.CanvasVideoRequest(prompt="test", provider_id="runninghub", model="model")

        with patch.object(main, "_canvas_video_impl", side_effect=error):
            await main.run_canvas_video_task(task_id, payload)

        task = main.canvas_task_get(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["phase"], "failed")
        self.assertFalse(task.get("upstream_task_id"))

    async def test_timeout_after_upstream_acceptance_stays_polling(self):
        task_id = "canvas_video_recoverable"
        main.canvas_task_create({
            "id": task_id,
            "status": "queued",
            "phase": "validating",
            "created_at": time.time(),
            "upstream_task_id": "upstream-123",
        })
        error = main.HTTPException(status_code=504, detail="upstream wait timed out")
        payload = main.CanvasVideoRequest(prompt="test", provider_id="runninghub", model="model")

        with patch.object(main, "_canvas_video_impl", side_effect=error):
            await main.run_canvas_video_task(task_id, payload)

        task = main.canvas_task_get(task_id)
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["phase"], "polling")
        self.assertEqual(task["upstream_task_id"], "upstream-123")

    async def test_explicit_upstream_failure_stops_polling(self):
        task_id = "canvas_video_explicit_failure"
        main.canvas_task_create({
            "id": task_id,
            "status": "running",
            "phase": "polling",
            "created_at": time.time(),
            "upstream_task_id": "upstream-456",
        })
        error = main.HTTPException(status_code=502, detail="RunningHub 任务失败：审核拒绝")
        payload = main.CanvasVideoRequest(prompt="test", provider_id="runninghub", model="model")

        with patch.object(main, "_canvas_video_impl", side_effect=error):
            await main.run_canvas_video_task(task_id, payload)

        task = main.canvas_task_get(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["phase"], "failed")

    async def test_recovery_dispatches_non_runninghub_task_to_generic_adapter(self):
        task = {
            "id": "canvas_video_generic_recovery",
            "type": "online-video",
            "status": "running",
            "phase": "polling",
            "provider_id": "openai-compatible",
            "upstream_task_id": "video-123",
        }
        with patch.object(main, "refresh_runninghub_canvas_task") as runninghub:
            with patch.object(main, "upstream_context_for_profile_id", side_effect=RuntimeError("stop after dispatch")):
                result = await main.refresh_canvas_cloud_task(task)
        runninghub.assert_not_called()
        self.assertEqual(result["id"], task["id"])


class RunningHubVideoOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_entries_keep_video_and_last_frame_separate(self):
        raw = {
            "results": [
                {"url": "https://example.test/out.mp4", "outputType": "mp4", "name": "videoUrl"},
                {"url": "https://example.test/frame.jpg", "outputType": "jpg", "name": "lastFrameUrl"},
            ]
        }
        entries = main.runninghub_video_output_entries(raw)
        self.assertEqual([item["output_type"] for item in entries], ["mp4", "jpg"])

    async def test_remote_video_output_detects_jpeg_as_last_frame(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"test-jpeg"
        response = SimpleNamespace(content=jpeg, raise_for_status=lambda: None, headers={"content-type": "image/jpeg"})
        client = SimpleNamespace(get=lambda *args, **kwargs: asyncio.sleep(0, result=response))
        with tempfile.TemporaryDirectory() as output_dir, patch.object(main, "OUTPUT_OUTPUT_DIR", output_dir):
            item = await main.save_runninghub_video_media_output(
                client,
                "https://example.test/frame.jpg",
                "jpg",
                "lastFrameUrl",
            )
        self.assertEqual(item["kind"], "image")
        self.assertTrue(item["is_last_frame"])


if __name__ == "__main__":
    unittest.main()
