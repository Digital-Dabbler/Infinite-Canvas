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


if __name__ == "__main__":
    unittest.main()
