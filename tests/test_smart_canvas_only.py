import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class SmartCanvasOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.workspace_js = (cls.root / "static" / "js" / "canvas-list.js").read_text(encoding="utf-8")
        cls.smart_canvas_js = (cls.root / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_classic_canvas_static_resources_are_removed(self):
        self.assertFalse((self.root / "static" / "canvas.html").exists())
        self.assertFalse((self.root / "static" / "js" / "canvas.js").exists())
        self.assertFalse((self.root / "static" / "css" / "canvas.css").exists())
        self.assertFalse((self.root / "static" / "js" / "i18n" / "canvas.js").exists())

    def test_canvas_creation_accepts_only_smart_kind(self):
        payload = main.CanvasCreateRequest(title="智能画布", kind="smart")
        with mock.patch.object(main, "new_canvas", return_value={"id": "smart-1", "kind": "smart"}):
            result = asyncio.run(main.create_canvas(payload))
        self.assertEqual(result["canvas"]["kind"], "smart")

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main.create_canvas(main.CanvasCreateRequest(title="不支持的画布", kind="unsupported")))
        self.assertEqual(caught.exception.status_code, 400)

    def test_project_workspace_opens_only_smart_canvas(self):
        self.assertIn("/static/smart-canvas.html", self.workspace_js)
        self.assertNotIn("/static/canvas.html", self.workspace_js)
        self.assertNotIn("ws-legacy-section", self.workspace_js)

    def test_composer_run_uses_its_own_node_context_and_snapshot(self):
        source = self.smart_canvas_js
        run_start = source.index("async function runGeneration(targetNode=null){")
        run_end = source.index("const SMART_TEXT_GENERATION_TIMEOUT_MS", run_start)
        run_body = source[run_start:run_end]

        self.assertIn("const composerContext = {nodeId:'', revision:0};", source)
        self.assertIn("const node = targetNode || activeComposerNode();", run_body)
        self.assertNotIn("const node = selectedNode();", run_body)
        self.assertIn("const request = buildRunRequestForNode(node, null, true, smartLoopContext);", run_body)
        self.assertIn("smartRunSnapshot(node, prompt, refs, logKind, runSettings)", run_body)
        self.assertIn("clearPromptInputForNode(node, {preserveDraft:true});", run_body)
        self.assertIn("await runGeneration(node);", source)

    def test_recoverable_upstream_task_stops_the_live_timer(self):
        source = self.smart_canvas_js
        recover_start = source.index("if(e && e.imageTaskRecover && e.recoverTaskId){")
        recover_end = source.index("return;", recover_start)
        recover_body = source[recover_start:recover_end]
        self.assertIn("node.pending = 0;", recover_body)
        self.assertIn("node.running = false;", recover_body)

    def test_canvas_media_catalog_preserves_replaced_media_for_authorized_lookup(self):
        canvas = {
            "nodes": [],
            "media_catalog": [{"url": "/output/previous-image.png", "kind": "image"}],
        }
        self.assertTrue(main.canvas_contains_media(canvas, "/output/previous-image.png"))

    def test_canvas_save_contract_and_picker_include_media_catalog(self):
        payload = main.CanvasSaveRequest(media_catalog=[{"url": "/output/previous-image.png"}])
        self.assertEqual(payload.media_catalog[0]["url"], "/output/previous-image.png")
        self.assertIn("function mergeCanvasMediaCatalog", self.smart_canvas_js)
        self.assertIn("media_catalog:storageCanvas.media_catalog || []", self.smart_canvas_js)
        self.assertIn("function removeCanvasMediaCatalogItem", self.smart_canvas_js)

    def test_comfy_workflow_sources_are_separated_with_legacy_system_names(self):
        system_name = "comfyui-workflow-multiple-angles-api.json"
        self.assertEqual(
            main.normalized_workflow_name(system_name),
            f"system/{system_name}",
        )
        system_items = main.list_workflows(source="system")["workflows"]
        custom_items = main.list_workflows(source="custom")["workflows"]
        self.assertTrue(system_items)
        self.assertTrue(all(item["source"] == "system" for item in system_items))
        self.assertTrue(all(item["source"] == "custom" for item in custom_items))
        with self.assertRaises(HTTPException) as caught:
            main.delete_workflow(f"system/{system_name}")
        self.assertEqual(caught.exception.status_code, 400)

    def test_comfy_execution_failover_only_accepts_explicit_node_errors(self):
        execution_error = {
            "status": {
                "messages": [["execution_error", {
                    "node_id": "2",
                    "node_type": "Mask_Remove_bg2",
                    "exception_message": "model file not found",
                }]],
            },
        }
        self.assertIn("model file not found", main.comfy_history_execution_failure(execution_error))
        self.assertEqual(main.comfy_history_execution_failure({"outputs": {}}), "")
        self.assertEqual(
            main.comfy_history_execution_failure({"status": {"messages": [["execution_cached", {"node_id": "2"}]]}}),
            "",
        )

    def test_comfy_execution_failover_walks_all_remaining_backends(self):
        self.assertEqual(
            main.comfy_execution_retry_backends(["first:8188", "second:8188", "third:8188"], "first:8188"),
            ["second:8188", "third:8188"],
        )
        self.assertEqual(main.comfy_execution_retry_backends(["first:8188"], "first:8188"), [])


if __name__ == "__main__":
    unittest.main()
