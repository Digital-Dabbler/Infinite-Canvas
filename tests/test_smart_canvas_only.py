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


if __name__ == "__main__":
    unittest.main()
