import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class CanvasPersistenceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.canvas_dir = os.path.join(self.temp_dir.name, "canvases")
        os.makedirs(self.canvas_dir)
        self.canvas_dir_patcher = patch.object(main, "CANVAS_DIR", self.canvas_dir)
        self.canvas_dir_patcher.start()

    def tearDown(self):
        self.canvas_dir_patcher.stop()
        self.temp_dir.cleanup()

    def test_save_canvas_atomically_replaces_complete_snapshot(self):
        canvas = {"id": "durable", "kind": "smart", "nodes": [], "connections": [], "logs": []}
        main.save_canvas(canvas)

        with open(main.canvas_path("durable"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["id"], "durable")
        self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(self.canvas_dir)))

    def test_corrupted_canvas_returns_controlled_conflict(self):
        with open(main.canvas_path("broken"), "w", encoding="utf-8") as handle:
            handle.write('{"id":"broken",')

        with self.assertRaises(main.HTTPException) as caught:
            main.load_canvas("broken")

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "canvas_corrupted")


if __name__ == "__main__":
    unittest.main()
