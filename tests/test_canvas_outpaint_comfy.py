import json
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW_NAME = "system/Qwen Image 2.1 扩图_无限画布专用api.json"
CONFIG_PATH = ROOT / "workflows" / "system" / "Qwen Image 2.1 扩图_无限画布专用api.config.json"


def extract_js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    j = source.index("{", start)
    while True:
        char = source[j]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start:j + 1]


def comfy_field_kind(field: dict) -> str:
    """镜像 smart-canvas.js comfyFieldKind 的判定，验证扩图 config 不产生 prompt 字段。"""
    if field.get("type") in ("image", "mask", "video", "audio"):
        return field["type"]
    key = f"{field.get('input', '')} {field.get('name', '')}".lower()
    if field.get("type") == "textarea" or re.search(r"prompt|text|提示词|正向|负向", key):
        return "prompt"
    return "setting"


def python_output_geometry(width, height, pads, maximum, multiple):
    """Goohaitools《外补画板（可视化）》_output_geometry 的逐字移植，作为 JS 版参考实现。"""
    def nearest(value):
        return int(value + 0.5)

    total_w = width + pads[0] + pads[1]
    total_h = height + pads[2] + pads[3]
    scale = min(1, maximum / max(total_w, total_h)) if maximum > 0 else 1
    horizontal = pads[0] + pads[1] > 0
    vertical = pads[2] + pads[3] > 0
    if multiple > 0 and horizontal != vertical:
        base = total_h if horizontal else total_w
        target = max(multiple, nearest(base * scale / multiple) * multiple)
        if maximum > 0 and target / base * max(total_w, total_h) > maximum:
            target = int(base * scale / multiple) * multiple
        if target > 0:
            scale = target / base
    elif multiple > 0 and not horizontal and not vertical:
        scale = max(scale, multiple / min(total_w, total_h))
    scaled_w = max(1, nearest(total_w * scale))
    scaled_h = max(1, nearest(total_h * scale))
    adjustments = [0, 0, 0, 0]
    if multiple > 0:
        for first, second, total, original in ((0, 1, scaled_w, total_w), (2, 3, scaled_h, total_h)):
            pad_total = pads[first] + pads[second]
            if not pad_total:
                if not horizontal and not vertical:
                    delta = max(multiple, int(total / multiple) * multiple) - total
                    adjustments[first] = -((-delta) // 2)
                    adjustments[second] = delta - adjustments[first]
                continue
            safe_first = int(pads[first] * total / max(1, original))
            safe_second = int(pads[second] * total / max(1, original))
            minimum = -(-((total - safe_first - safe_second) // multiple)) * multiple
            target = max(multiple, minimum, nearest(total / multiple) * multiple)
            delta = target - total
            amount = nearest(abs(delta) * pads[first] / pad_total)
            if delta < 0:
                amount = max(abs(delta) - safe_second, min(safe_first, amount))
            adjustments[first] = -amount if delta < 0 else amount
            adjustments[second] = delta - adjustments[first]
    return {
        "scaledWidth": scaled_w, "scaledHeight": scaled_h,
        "width": scaled_w + adjustments[0] + adjustments[1],
        "height": scaled_h + adjustments[2] + adjustments[3],
        "adjustments": adjustments,
        "totalWidth": total_w, "totalHeight": total_h,
    }


class CanvasOutpaintComfyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smart_canvas_js = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.smart_canvas_html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.i18n_js = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.workflow = json.loads((ROOT / "workflows" / WORKFLOW_NAME).read_text(encoding="utf-8"))

    def test_config_maps_editor_parameters_to_workflow_nodes(self):
        fields = {field["id"]: field for field in self.config["fields"]}
        self.assertEqual(fields["source_image"]["type"], "image")
        self.assertEqual((fields["source_image"]["node"], fields["source_image"]["input"]), ("56", "image"))
        self.assertEqual((fields["supplement_prompt"]["node"], fields["supplement_prompt"]["input"]), ("51", "value"))
        self.assertEqual((fields["seed"]["node"], fields["seed"]["input"]), ("18", "noise_seed"))
        self.assertTrue(fields["seed"].get("random_enabled"))
        self.assertEqual((fields["max_sample_size"]["node"], fields["max_sample_size"]["input"]), ("27", "值"))
        self.assertEqual(fields["max_sample_size"]["default"], 2048)
        self.assertEqual((fields["lora_strength"]["node"], fields["lora_strength"]["input"]), ("17", "strength_model"))

    def test_config_produces_no_prompt_kind_fields(self):
        # 补充提示词走 setting 通道：空提示词不得触发 smartRunNeedsPrompt 拦截。
        kinds = [comfy_field_kind(field) for field in self.config["fields"]]
        self.assertEqual(kinds, ["image", "setting", "setting", "setting", "setting"])

    def test_workflow_entry_and_mapped_nodes_are_intact(self):
        self.assertEqual(self.workflow["56"]["class_type"], "LoadImage")
        self.assertEqual(self.workflow["17"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(self.workflow["18"]["class_type"], "RandomNoise")
        self.assertEqual(self.workflow["51"]["class_type"], "PrimitiveStringMultiline")
        self.assertIn("值", self.workflow["27"]["inputs"])
        self.assertEqual(self.workflow["17"]["inputs"]["strength_model"], 0.7)

    def test_frontend_runs_outpaint_through_comfy_workflow(self):
        self.assertIn("const OUTPAINT_COMFY_WORKFLOW = 'system/Qwen Image 2.1 扩图_无限画布专用api.json';", self.smart_canvas_js)
        self.assertIn("const OUTPAINT_COMFY_FILL_COLOR = '#808080';", self.smart_canvas_js)
        self.assertIn("comfyWorkflow: OUTPAINT_COMFY_WORKFLOW,", self.smart_canvas_js)
        self.assertIn("comfyMode:'custom',", self.smart_canvas_js)
        self.assertIn("comfyParams: {supplement_prompt: supplementPrompt, max_sample_size: maxSample},", self.smart_canvas_js)
        self.assertIn("alpha:isComfy", self.smart_canvas_js)
        self.assertIn("移除图像中的${colorName}区域，无缝自然延展为完整图像。", self.smart_canvas_js)

    def test_frontend_locks_fill_and_count_in_comfy_mode(self):
        self.assertIn("button.disabled = isComfy;", self.smart_canvas_js)
        self.assertIn("setOutpaintFillColor(OUTPAINT_COMFY_FILL_COLOR)", self.smart_canvas_js)
        self.assertIn("editorSettings.count = 1;", self.smart_canvas_js)
        self.assertIn("tr('canvas.outpaintComfyWorkflowName')", self.smart_canvas_js)
        self.assertIn("runBtn.disabled = outpaintRunBtnBaseDisabled || !outpaintHasExtension();", self.smart_canvas_js)

    def test_frontend_editor_controls_and_i18n_are_wired(self):
        for element_id in (
            "imageOutpaintTools", "outpaintUnitSelect", "outpaintLeftInput", "outpaintRightInput",
            "outpaintTopInput", "outpaintBottomInput", "outpaintRatioSelect", "outpaintMaxLongInput",
            "outpaintDivisorSelect", "outpaintSupplementInput", "outpaintMaxSampleInput",
            "outpaintResetBtn", "outpaintColorLockNote",
        ):
            self.assertIn(f'id="{element_id}"', self.smart_canvas_html)
        self.assertIn("document.getElementById('imageOutpaintTools')?.classList.toggle('active', imageEditMode === 'outpaint');", self.smart_canvas_js)
        self.assertIn("syncOutpaintEditorControls();", self.smart_canvas_js)
        self.assertIn("resetOutpaintEditor", self.smart_canvas_html)
        for key in (
            "canvas.outpaintUnit", "canvas.outpaintUnitPercent", "canvas.outpaintUnitPixel",
            "canvas.outpaintPadLeft", "canvas.outpaintPadRight", "canvas.outpaintPadTop", "canvas.outpaintPadBottom",
            "canvas.outpaintRatio", "canvas.outpaintRatioFree", "canvas.outpaintMaxLong", "canvas.outpaintDivisor",
            "canvas.outpaintSupplement", "canvas.outpaintSupplementPlaceholder", "canvas.outpaintMaxSample",
            "canvas.outpaintReset", "canvas.outpaintComfyMode", "canvas.outpaintComfyWorkflowName",
            "canvas.outpaintComfyFillLocked", "canvas.outpaintNoExtension",
        ):
            self.assertIn(f'"canvas.' in key and f'"{key}"' or f'"{key}"', self.i18n_js)
        self.assertIn(".outpaint-color-control button:disabled", self.css)
        self.assertIn(".outpaint-color-lock", self.css)

    def test_outpaint_geometry_matches_goohai_python_reference(self):
        js = "\n".join([
            extract_js_function(self.smart_canvas_js, "outpaintNearest"),
            extract_js_function(self.smart_canvas_js, "outpaintOutputGeometry"),
            "const fs = require('fs');",
            "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));",
            "const out = cases.map(c => outpaintOutputGeometry(c[0], c[1], c[2], c[3], c[4]));",
            "fs.writeFileSync(process.argv[3], JSON.stringify(out));",
        ])
        rng = random.Random(20261108)
        cases = [
            [1024, 640, [307, 307, 0, 0], 5120, 0],
            [1024, 640, [307, 307, 0, 0], 5120, 32],
            [2006, 1254, [602, 602, 0, 0], 5120, 0],
            [1000, 1000, [0, 0, 0, 0], 0, 32],
            [1000, 1000, [0, 0, 0, 0], 512, 0],
            [640, 1024, [0, 0, 200, 300], 800, 16],
            [333, 555, [17, 23, 29, 31], 4096, 64],
        ]
        for _ in range(200):
            cases.append([
                rng.randint(1, 3000), rng.randint(1, 3000),
                [rng.randint(0, 1200) for _ in range(4)],
                rng.choice([0, 512, 1024, 5120, 8192]),
                rng.choice([0, 8, 16, 32, 64]),
            ])
        with tempfile.TemporaryDirectory() as tmp:
            js_path = Path(tmp) / "geometry.js"
            cases_path = Path(tmp) / "cases.json"
            out_path = Path(tmp) / "out.json"
            js_path.write_text(js, encoding="utf-8")
            cases_path.write_text(json.dumps(cases), encoding="utf-8")
            subprocess.run(["node", str(js_path), str(cases_path), str(out_path)], check=True)
            results = json.loads(out_path.read_text(encoding="utf-8"))
        for case, result in zip(cases, results):
            expected = python_output_geometry(*case)
            self.assertEqual(result, expected, f"case={case}")

    def test_outpaint_geometry_output_is_divisible_when_requested(self):
        for case in ([1024, 640, [307, 307, 0, 0], 5120, 32], [640, 1024, [0, 0, 200, 300], 0, 16], [777, 555, [100, 0, 0, 133], 4096, 8]):
            expected = python_output_geometry(*case)
            multiple = case[4]
            self.assertEqual(expected["width"] % multiple, 0, case)
            self.assertEqual(expected["height"] % multiple, 0, case)


if __name__ == "__main__":
    unittest.main()
