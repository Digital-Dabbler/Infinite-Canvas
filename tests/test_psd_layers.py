# -*- coding: utf-8 -*-
"""psd_layers 纯函数离线单元测试（不联网、不读盘）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psd_layers


def _real_like_results():
    """形态对齐 2026-11-11/12 两次实测成功任务的 results[]。"""
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "taskId": "2120440366181736450",
            "status": "SUCCESS",
            "results": [
                {"url": "https://rh.example/base.png", "zindex": 0, "size": "1199x1200",
                 "background": "opaque", "name": "", "description": "整幅背景"},
                {"url": "https://rh.example/title.png", "zindex": 1,
                 "name": "标题字", "description": "顶部标题",
                 "size": "391x290", "background": "transparent",
                 "boundingBox": {"absolute": [398, 0, 789, 290], "normalized": [331, 0, 656, 241]}},
                {"url": "https://rh.example/person.png", "zindex": 2,
                 "name": "人物", "size": "440x600", "background": "transparent",
                 "boundingBox": {"absolute": [120, 300, 560, 900], "normalized": [100, 250, 466, 750]}},
            ],
        },
    }


class BuildLayerBodyTest(unittest.TestCase):
    def test_default_body_matches_product_contract(self):
        body = psd_layers.build_layer_body("https://rh.example/in.png")
        self.assertEqual(body["imageUrl"], "https://rh.example/in.png")
        self.assertEqual(body["resolution"], "auto")
        self.assertEqual(body["outputFormat"], "png")
        self.assertEqual(body["watermark"], "false")
        self.assertEqual(body["textRecognition"], "false")
        self.assertEqual(body["maxImages"], psd_layers.LAYER_DEFAULT_MAX_IMAGES)
        self.assertNotIn("prompt", body)
        # 企业共享钱包只走 Bearer；body 里绝不能出现 apiKey。
        self.assertNotIn("apiKey", body)

    def test_prompt_truncated_and_resolution_lowercased(self):
        body = psd_layers.build_layer_body("u", prompt="x" * 3000, resolution="2K")
        self.assertEqual(len(body["prompt"]), psd_layers.LAYER_PROMPT_MAX)
        self.assertEqual(body["resolution"], "2k")

    def test_max_images_clamped(self):
        self.assertEqual(psd_layers.build_layer_body("u", max_images=999)["maxImages"],
                         psd_layers.LAYER_MAX_MAX_IMAGES)
        self.assertEqual(psd_layers.build_layer_body("u", max_images=1)["maxImages"],
                         psd_layers.LAYER_MIN_MAX_IMAGES)
        self.assertEqual(psd_layers.build_layer_body("u", max_images="abc")["maxImages"],
                         psd_layers.LAYER_DEFAULT_MAX_IMAGES)

    def test_empty_url_rejected(self):
        with self.assertRaises(ValueError):
            psd_layers.build_layer_body("  ")


class ParseResultsTest(unittest.TestCase):
    def test_real_like_payload(self):
        parsed = psd_layers.parse_layer_decomposition_results(_real_like_results(), 1199, 1200, id_seed="t1")
        layers = parsed["layers"]
        self.assertEqual(parsed["strategy"], "result_fields")
        self.assertEqual(len(layers), 3)
        base, title, person = layers
        self.assertTrue(base["isBase"])
        self.assertEqual((base["x"], base["y"], base["width"], base["height"]), (0, 0, 1199, 1200))
        self.assertEqual(base["name"], "背景")
        self.assertEqual(title["name"], "标题字")
        self.assertEqual((title["x"], title["y"], title["width"], title["height"]), (398, 0, 391, 290))
        self.assertEqual((person["x"], person["y"], person["width"], person["height"]), (120, 300, 440, 600))
        self.assertEqual([layer["zIndex"] for layer in layers], [0, 1, 2])
        self.assertTrue(all(layer["visible"] for layer in layers))
        self.assertTrue(all(layer["id"] for layer in layers))
        self.assertEqual(layers[0]["id"], "base_0_t1")

    def test_normalized_only_box_converted_to_pixels(self):
        payload = {"results": [
            {"url": "b.png", "size": "2000x1000", "background": "opaque"},
            {"url": "a.png", "size": "400x300",
             "boundingBox": {"normalized": [250, 100, 450, 400]}},
        ]}
        parsed = psd_layers.parse_layer_decomposition_results(payload, 2000, 1000)
        layer = parsed["layers"][1]
        self.assertEqual((layer["x"], layer["y"], layer["width"], layer["height"]), (500, 100, 400, 300))

    def test_flat_sequence_bbox_is_pixels(self):
        payload = [{"url": "b.png", "size": "800x600", "background": "opaque"},
                   {"url": "a.png", "boundingBox": [10, 20, 110, 120], "size": "100x100"}]
        parsed = psd_layers.parse_layer_decomposition_results(payload, 800, 600)
        self.assertEqual(parsed["layers"][1]["x"], 10)
        self.assertEqual(parsed["layers"][1]["y"], 20)

    def test_upstream_canvas_larger_than_local_source(self):
        # 实测 ZEUS：本地源图 735×836，上游重绘画布 960×1092，absolute 包围盒是画布像素空间。
        # 坐标空间必须取上游画布，否则所有图层按 1.3 倍溢出，预览上下被裁。
        payload = {"results": [
            {"url": "b.png", "zindex": 0, "background": "opaque", "size": "960x1092"},
            {"url": "l1.png", "name": "左柱", "zindex": 1, "size": "379x1092",
             "boundingBox": {"absolute": [0, 0, 379, 1092]}},
            {"url": "l7.png", "name": "底栏符号", "zindex": 7, "size": "777x334",
             "boundingBox": {"absolute": [89, 742, 866, 1076]}},
        ]}
        parsed = psd_layers.parse_layer_decomposition_results(payload, 735, 836, id_seed="z1")
        self.assertEqual(parsed["canvasSize"], {"width": 960, "height": 1092})
        base = parsed["layers"][0]
        self.assertEqual((base["x"], base["y"], base["width"], base["height"]), (0, 0, 960, 1092))
        bottom = parsed["layers"][2]
        self.assertEqual((bottom["x"], bottom["y"], bottom["width"], bottom["height"]), (89, 742, 777, 334))

    def test_canvas_falls_back_to_absolute_extent(self):
        # 底图项没有 size 字段时，用所有 absolute 盒的最大范围推断画布。
        payload = {"results": [
            {"url": "b.png", "zindex": 0, "background": "opaque"},
            {"url": "l1.png", "zindex": 1, "boundingBox": {"absolute": [0, 0, 500, 700]}},
        ]}
        parsed = psd_layers.parse_layer_decomposition_results(payload, 400, 600)
        self.assertEqual(parsed["canvasSize"], {"width": 500, "height": 700})

    def test_array_order_used_when_no_explicit_z(self):
        payload = {"results": [
            {"url": "b.png", "size": "100x100", "background": "opaque", "boundingBox": {"normalized": [0, 0, 1000, 1000]}},
            {"url": "a.png", "size": "10x10", "boundingBox": {"absolute": [0, 0, 10, 10]}},
            {"url": "c.png", "size": "20x20", "boundingBox": {"absolute": [5, 5, 25, 25]}},
        ]}
        parsed = psd_layers.parse_layer_decomposition_results(payload, 100, 100)
        self.assertEqual(parsed["strategy"], "result_fields")
        self.assertEqual([layer["url"] for layer in parsed["layers"]], ["b.png", "a.png", "c.png"])

    def test_base_relocated_to_front(self):
        payload = {"results": [
            {"url": "a.png", "zindex": 2, "boundingBox": {"absolute": [1, 1, 9, 9]}},
            {"url": "b.png", "zindex": 0, "size": "100x100", "background": "opaque"},
            {"url": "c.png", "zindex": 1, "boundingBox": {"absolute": [2, 2, 8, 8]}},
        ]}
        parsed = psd_layers.parse_layer_decomposition_results(payload, 100, 100)
        self.assertEqual(parsed["layers"][0]["url"], "b.png")
        self.assertTrue(parsed["layers"][0]["isBase"])
        self.assertEqual([layer["zIndex"] for layer in parsed["layers"]], [0, 1, 2])

    def test_no_base_anywhere_falls_back_to_last(self):
        payload = {"results": [
            {"url": "a.png", "boundingBox": {"absolute": [0, 0, 50, 50]}},
            {"url": "b.png", "boundingBox": {"absolute": [10, 10, 60, 60]}},
        ]}
        parsed = psd_layers.parse_layer_decomposition_results(payload, 100, 100)
        self.assertEqual(parsed["layers"][0]["url"], "b.png")
        self.assertTrue(parsed["layers"][0]["isBase"])
        self.assertEqual(parsed["layers"][0]["name"], "背景")

    def test_empty_results(self):
        parsed = psd_layers.parse_layer_decomposition_results({"data": {"results": []}}, 10, 10)
        self.assertEqual(parsed["layers"], [])
        self.assertEqual(parsed["strategy"], "empty")
        self.assertEqual(parse_out := parsed["baseIndex"], -1)


class NormalizePsdLayersTest(unittest.TestCase):
    def test_reorder_rename_and_visibility_roundtrip(self):
        psd = {"size": {"width": 100, "height": 100}, "layers": [
            {"id": "base_0_x", "name": "背景", "url": "b", "x": 0, "y": 0, "width": 100, "height": 100,
             "visible": True, "isBase": True},
            {"id": "layer_1_x", "name": "A", "url": "a", "x": 5, "y": "6", "width": 10, "height": 10,
             "visible": False, "isBase": False},
            {"id": "layer_2_x", "name": "B", "url": "c", "x": 1, "y": 2, "width": 3, "height": 4,
             "visible": True, "isBase": False},
        ]}
        moved = dict(psd)
        # 模拟用户把 B 调到 A 下面：数组顺序变化后重编号。
        moved["layers"] = [psd["layers"][0], psd["layers"][2], psd["layers"][1]]
        normalized = psd_layers.normalize_psd_layers(moved)
        self.assertEqual([layer["name"] for layer in normalized["layers"]], ["背景", "B", "A"])
        self.assertEqual([layer["zIndex"] for layer in normalized["layers"]], [0, 1, 2])
        self.assertEqual(normalized["layers"][2]["y"], 6)
        self.assertIs(normalized["layers"][1]["visible"], True)
        self.assertFalse(normalized["layers"][2]["visible"])

    def test_base_always_first(self):
        normalized = psd_layers.normalize_psd_layers({"layers": [
            {"name": "A", "url": "a", "isBase": False},
            {"name": "背景", "url": "b", "isBase": True},
        ]})
        self.assertEqual([layer["name"] for layer in normalized["layers"]], ["背景", "A"])
        self.assertTrue(normalized["layers"][0]["isBase"])

    def test_non_dict_returns_empty(self):
        self.assertEqual(psd_layers.normalize_psd_layers(None), {})


if __name__ == "__main__":
    unittest.main()
