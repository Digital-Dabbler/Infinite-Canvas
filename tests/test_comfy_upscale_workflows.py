import json
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class ComfyUpscaleWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.smart_canvas_js = (cls.root / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.smart_canvas_html = (cls.root / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.i18n_js = (cls.root / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.vosr2_path = cls.root / "workflows" / "system" / "高清修复-VOSR2_api.json"

    def load_vosr2(self):
        return json.loads(self.vosr2_path.read_text(encoding="utf-8"))

    def test_vosr2_workflow_is_api_format_with_load_and_save_nodes(self):
        workflow = self.load_vosr2()
        self.assertTrue(workflow)
        for node_id, node in workflow.items():
            self.assertIsInstance(node, dict, node_id)
            self.assertIn("class_type", node, node_id)
            self.assertIn("inputs", node, node_id)
        self.assertEqual(workflow["3"]["class_type"], "LoadImage")
        self.assertEqual(workflow["5"]["class_type"], "SaveImage")
        self.assertEqual(workflow["1"]["class_type"], "TESpeedVOSR2Image")

    def test_vosr2_is_registered_as_builtin_upscale_workflow(self):
        self.assertEqual(main.COMFY_UPSCALE_INPUT_NODES["system/高清修复-VOSR2_api.json"], "3")
        self.assertEqual(main.COMFY_UPSCALE_INPUT_NODES["system/2K高清放大-Klein 9B-api.json"], "157")

    def test_upscale_input_validation_covers_vosr2(self):
        folder, name = main.comfy_staging_upload_target("image.png")
        valid = main.GenerateRequest(
            workflow_json="system/高清修复-VOSR2_api.json",
            params={"3": {"image": f"{folder}/{name}"}},
        )
        main.require_comfy_upscale_input(valid)
        with self.assertRaises(HTTPException) as caught:
            main.require_comfy_upscale_input(main.GenerateRequest(
                workflow_json="system/高清修复-VOSR2_api.json",
                params={"3": {"image": "../outside.png"}},
            ))
        self.assertEqual(caught.exception.status_code, 400)

    def test_frontend_only_nodes_are_pruned_only_when_unreferenced(self):
        workflow = {
            "1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
            "2": {"class_type": "ImageCompare", "inputs": {"image_a": ["3", 0]}},
            "3": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "4": {"class_type": "ImageCompare", "inputs": {"image_a": ["3", 0]}},
            "5": {"class_type": "Note", "inputs": {"text": "备注"}},
        }
        removed = main.prune_comfy_frontend_only_nodes(workflow)
        # 4/5 是终端节点，直接移除；2 被 SaveImage 引用，保留，避免留下悬空连线。
        self.assertEqual(sorted(removed), ["4", "5"])
        self.assertIn("2", workflow)
        self.assertNotIn("4", workflow)
        self.assertNotIn("5", workflow)

    def test_vosr2_prune_keeps_save_image_path_intact(self):
        workflow = self.load_vosr2()
        main.prune_comfy_frontend_only_nodes(workflow)
        self.assertEqual(sorted(workflow.keys()), ["1", "2", "3", "4", "5", "7"])
        self.assertEqual(workflow["5"]["inputs"]["images"], ["1", 0])
        self.assertEqual(workflow["1"]["inputs"]["images"], ["7", 0])
        self.assertEqual(workflow["7"]["inputs"]["image"], ["3", 0])

    def test_upscale_menu_exposes_vosr2_and_renames_comfy_option(self):
        self.assertIn('data-upscale-mode="vosr2"', self.smart_canvas_html)
        self.assertIn('data-upscale-mode="comfy"', self.smart_canvas_html)
        self.assertIn("Flux2-Klein", self.smart_canvas_html)
        self.assertNotIn("<span>ComfyUI</span>", self.smart_canvas_html)
        self.assertIn('"smart.upscaleModeComfy": { zh: "Flux2-Klein", en: "Flux2-Klein" }', self.i18n_js)
        self.assertIn('"smart.upscaleModeVosr2"', self.i18n_js)

    def test_smart_canvas_dispatches_vosr2_to_its_own_workflow(self):
        self.assertIn("const SMART_VOSR2_UPSCALE_WORKFLOW = 'system/高清修复-VOSR2_api.json';", self.smart_canvas_js)
        self.assertIn("function smartUpscaleWorkflowForMode(mode)", self.smart_canvas_js)
        self.assertIn("if(mode === 'comfy' || mode === 'vosr2') return true;", self.smart_canvas_js)
        self.assertIn("runSmartComfyUpscaleAction(node, index, resolvedMode);", self.smart_canvas_js)
        self.assertIn("workflow_json:workflow,", self.smart_canvas_js)
        self.assertIn("params:{[inputNode]:{image:inputName}},", self.smart_canvas_js)


if __name__ == "__main__":
    unittest.main()