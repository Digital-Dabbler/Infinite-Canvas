import json
import logging
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

    def test_successful_canvas_operations_are_quiet_but_failures_remain_visible(self):
        def access_record(status):
            record = logging.LogRecord(
                "uvicorn.access", logging.INFO, __file__, 1,
                '%s - "%s %s HTTP/%s" %s', (), None,
            )
            record.args = ("127.0.0.1:1", "POST", "/api/canvases/a/operations", "1.1", str(status))
            return record

        access_filter = main.QuietAccessLogFilter()
        self.assertFalse(access_filter.filter(access_record(200)))
        self.assertTrue(access_filter.filter(access_record(403)))


if __name__ == "__main__":
    unittest.main()
