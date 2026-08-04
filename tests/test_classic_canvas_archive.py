import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class ClassicCanvasArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.canvas_html = (root / "static" / "canvas.html").read_text(encoding="utf-8")
        cls.canvas_js = (root / "static" / "js" / "canvas.js").read_text(encoding="utf-8")
        cls.canvas_list_html = (root / "static" / "canvas-list.html").read_text(encoding="utf-8")
        cls.canvas_list_js = (root / "static" / "js" / "canvas-list.js").read_text(encoding="utf-8")
        cls.canvas_list_css = (root / "static" / "css" / "canvas-list.css").read_text(encoding="utf-8")

    def test_classic_canvas_referer_is_detected(self):
        request = SimpleNamespace(headers={"referer": "http://127.0.0.1:3000/static/canvas.html?id=old"})
        self.assertTrue(main.is_classic_canvas_page_request(request))

    def test_smart_canvas_referer_is_not_detected(self):
        request = SimpleNamespace(headers={"referer": "http://127.0.0.1:3000/static/smart-canvas.html?id=new"})
        self.assertFalse(main.is_classic_canvas_page_request(request))

    def test_classic_canvas_creation_is_rejected(self):
        payload = main.CanvasCreateRequest(title="旧画布", kind="classic")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(main.create_canvas(payload))
        self.assertEqual(caught.exception.status_code, 410)

    def test_smart_canvas_creation_remains_available(self):
        payload = main.CanvasCreateRequest(title="智能画布", kind="smart")
        with mock.patch.object(main, "new_canvas", return_value={"id": "smart-1", "kind": "smart"}):
            result = asyncio.run(main.create_canvas(payload))
        self.assertEqual(result["canvas"]["kind"], "smart")

    def test_project_workspace_only_creates_smart_canvases(self):
        self.assertNotIn('data-kind="classic"', self.canvas_list_js)
        self.assertNotIn("kind: isSmart ? 'smart' : 'classic'", self.canvas_list_js)
        self.assertIn("kind: 'smart'", self.canvas_list_js)
        self.assertIn("新建智能画布", self.canvas_list_html)

    def test_project_workspace_creation_is_a_centered_modal(self):
        self.assertIn("overlay.className = 'ws-create-overlay'", self.canvas_list_js)
        self.assertIn("workspace.appendChild(overlay)", self.canvas_list_js)
        self.assertIn("openCanvas(nc)", self.canvas_list_js)
        self.assertIn("backdrop-filter:blur(12px)", self.canvas_list_css)
        self.assertIn("width:min(520px, 100%)", self.canvas_list_css)

    def test_archive_prompt_requires_user_confirmation(self):
        self.assertIn('id="classicCanvasArchiveModal"', self.canvas_html)
        self.assertIn("showClassicCanvasArchivePrompt()", self.canvas_html)
        self.assertIn("function createSmartCanvasFromArchive()", self.canvas_js)
        self.assertNotIn("setClassicCanvasArchived(isClassicCanvas);\n        await createSmartCanvasFromArchive", self.canvas_js)

    def test_archive_creation_stays_in_context_before_opening(self):
        self.assertIn('id="classicCanvasCreateForm"', self.canvas_html)
        self.assertIn("async function submitSmartCanvasFromArchive(event)", self.canvas_js)
        self.assertIn("'X-Canvas-Archive-Action':'create-smart'", self.canvas_js)
        self.assertNotIn("target.searchParams.set('create', 'smart')", self.canvas_js)
        self.assertIn("openSmartCanvasPage(data.canvas.id, projectId)", self.canvas_js)


if __name__ == "__main__":
    unittest.main()
