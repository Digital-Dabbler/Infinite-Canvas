import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main


class CanvasAssetIndexCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.canvas_dir = os.path.join(self.temp_dir.name, "canvases")
        self.cache_path = os.path.join(self.temp_dir.name, "canvas_asset_index_cache.json")
        os.makedirs(self.canvas_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_canvas(self, canvas_id, images):
        path = os.path.join(self.canvas_dir, f"{canvas_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "id": canvas_id,
                    "title": canvas_id,
                    "kind": "smart",
                    "updated_at": len(images),
                    "nodes": [
                        {
                            "id": f"node-{canvas_id}",
                            "type": "smart-image-generation",
                            "images": [{"url": url, "kind": "image"} for url in images],
                        }
                    ],
                },
                handle,
                ensure_ascii=False,
            )
        return path

    def test_unchanged_canvases_reuse_cached_asset_entries(self):
        first_path = self.write_canvas("first", ["/assets/output/first.png"])
        self.write_canvas("second", ["/assets/output/second.png"])

        with (
            patch.object(main, "CANVAS_DIR", self.canvas_dir),
            patch.object(main, "CANVAS_ASSET_INDEX_CACHE_PATH", self.cache_path),
            patch.object(main, "cleanup_expired_canvas_trash", lambda: None),
            patch.object(main, "extract_canvas_assets", wraps=main.extract_canvas_assets) as extractor,
        ):
            first = main.canvas_assets_index()
            second = main.canvas_assets_index()
            self.assertEqual(extractor.call_count, 2)
            self.assertEqual(first, second)

            with open(first_path, "r", encoding="utf-8") as handle:
                changed = json.load(handle)
            changed["nodes"][0]["images"].append(
                {"url": "/assets/output/first-extra.png", "kind": "image"}
            )
            with open(first_path, "w", encoding="utf-8") as handle:
                json.dump(changed, handle, ensure_ascii=False)

            refreshed = main.canvas_assets_index()
            self.assertEqual(extractor.call_count, 3)
            self.assertEqual(len(refreshed["items"]), 3)


class SmartCanvasPerformanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canvas_js = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.shell_js = (ROOT / "static" / "js" / "smart-canvas-shell.js").read_text(encoding="utf-8")
        cls.assets_js = (ROOT / "static" / "js" / "asset-manager.js").read_text(encoding="utf-8")

    def test_grid_snap_requires_explicit_opt_in(self):
        self.assertIn("localStorage.getItem(SNAP_KEY) === '1'", self.shell_js)

    def test_canvas_media_stays_on_bounded_previews(self):
        self.assertIn("画布只使用尺寸受控的预览图", self.canvas_js)
        self.assertIn("if(preview && img.getAttribute('src') !== preview) img.src = preview", self.canvas_js)
        self.assertIn('loading="lazy"', self.canvas_js)
        self.assertIn('decoding="async"', self.canvas_js)

    def test_generation_thumbnail_strip_is_windowed(self):
        self.assertIn("const thumbEnd = Math.min(imgs.length, thumbStart + 9)", self.canvas_js)
        self.assertIn("imgs.slice(thumbStart, thumbEnd)", self.canvas_js)

    def test_asset_manager_initially_loads_only_the_active_tab(self):
        self.assertIn("loadAll({force:false})", self.assets_js)
        self.assertIn("if(activeTab === 'canvas-assets')", self.assets_js)


if __name__ == "__main__":
    unittest.main()
