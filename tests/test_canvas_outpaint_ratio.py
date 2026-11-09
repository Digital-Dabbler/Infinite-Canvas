"""扩图固定比例的联动测试：拖动把手、改数值框后四边仍严格保持所选比例。"""

import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RATIOS = {
    "1:1": [1, 1],
    "2:3": [2, 3],
    "3:2": [3, 2],
    "3:4": [3, 4],
    "4:3": [4, 3],
    "5:7": [5, 7],
    "9:16": [9, 16],
    "16:9": [16, 9],
    "21:9": [21, 9],
    "1:2": [1, 2],
    "2:1": [2, 1],
}

HANDLES = [
    "outpaint-top", "outpaint-right", "outpaint-bottom", "outpaint-left",
    "outpaint-top-left", "outpaint-top-right", "outpaint-bottom-left", "outpaint-bottom-right",
]


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


class CanvasOutpaintRatioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js_source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")

    def run_js(self, expression: str, cases: list):
        js = "\n".join([
            extract_js_function(self.js_source, "outpaintRatioLockedPads"),
            extract_js_function(self.js_source, "outpaintRatioPadInputPads"),
            "const fs = require('fs');",
            "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));",
            f"const out = cases.map(c => {expression});",
            "fs.writeFileSync(process.argv[3], JSON.stringify(out));",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            js_path = Path(tmp) / "ratio.js"
            cases_path = Path(tmp) / "cases.json"
            out_path = Path(tmp) / "out.json"
            js_path.write_text(js, encoding="utf-8")
            cases_path.write_text(json.dumps(cases), encoding="utf-8")
            subprocess.run(["node", str(js_path), str(cases_path), str(out_path)], check=True)
            return json.loads(out_path.read_text(encoding="utf-8"))

    def assert_ratio_exact(self, case, pads, ratio):
        src_w, src_h = case[0], case[1]
        rw, rh = ratio
        left, right, top, bottom = pads["left"], pads["right"], pads["top"], pads["bottom"]
        for value in (left, right, top, bottom):
            self.assertIsInstance(value, int, f"外补必须是整数: {case} -> {pads}")
            self.assertGreaterEqual(value, 0, f"外补不能为负: {case} -> {pads}")
        total_w = src_w + left + right
        total_h = src_h + top + bottom
        self.assertGreaterEqual(total_w, src_w)
        self.assertGreaterEqual(total_h, src_h)
        self.assertEqual(total_w * rh, total_h * rw, f"比例必须精确等于 {rw}:{rh}: {case} -> {pads}")

    def drag_cases(self):
        rng = random.Random(20261109)
        cases = []
        for _ in range(160):
            ratio_name = rng.choice(list(RATIOS))
            mode = rng.choice(HANDLES)
            shift = rng.random() < 0.25
            case = [
                rng.randint(1, 3000), rng.randint(1, 3000),
                {key: rng.randint(0, 900) for key in ("left", "right", "top", "bottom")},
                mode, rng.randint(-900, 900), rng.randint(-900, 900), shift,
                RATIOS[ratio_name][0], RATIOS[ratio_name][1],
            ]
            cases.append(case)
        # 截图场景：竖构图已选 3:2，再拖动右侧把手。
        cases.append([1975, 2147, {"left": 0, "right": 0, "top": 0, "bottom": 0}, "outpaint-right", 420, 0, False, 3, 2])
        return cases

    def test_drag_keeps_selected_ratio(self):
        cases = self.drag_cases()
        results = self.run_js(
            "outpaintRatioLockedPads(c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8])",
            cases,
        )
        for case, pads in zip(cases, results):
            ratio = (case[7], case[8])
            self.assert_ratio_exact(case, pads, ratio)

    def test_drag_keeps_the_opposite_side_fixed(self):
        cases = [case for case in self.drag_cases() if not case[6]]
        results = self.run_js(
            "outpaintRatioLockedPads(c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8])",
            cases,
        )
        for case, pads in zip(cases, results):
            mode = case[3]
            base = case[2]
            if "left" in mode and "right" not in mode:
                self.assertEqual(pads["right"], base["right"], f"拖左侧时右侧不能动: {case} -> {pads}")
            if "right" in mode and "left" not in mode:
                self.assertEqual(pads["left"], base["left"], f"拖右侧时左侧不能动: {case} -> {pads}")
            if "top" in mode and "bottom" not in mode:
                self.assertEqual(pads["bottom"], base["bottom"], f"拖上侧时下侧不能动: {case} -> {pads}")
            if "bottom" in mode and "top" not in mode:
                self.assertEqual(pads["top"], base["top"], f"拖下侧时上侧不能动: {case} -> {pads}")

    def test_dragging_outward_grows_and_inward_cannot_shrink_below_source(self):
        src_w, src_h = 1200, 800
        base = {"left": 0, "right": 0, "top": 0, "bottom": 0}
        out = self.run_js(
            "outpaintRatioLockedPads(c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8])",
            [
                [src_w, src_h, base, "outpaint-right", 600, 0, False, 3, 2],
                [src_w, src_h, base, "outpaint-right", 0, 0, False, 3, 2],
                [src_w, src_h, base, "outpaint-right", -900, 0, False, 3, 2],
                # 拖左侧把手向外 = dx 为负（与自由拖拽的方向约定一致）
                [src_w, src_h, base, "outpaint-left", -600, 0, False, 3, 2],
            ],
        )
        grown, untouched, shrunk, dragged_left = out
        self.assertGreater(grown["right"] + src_w, src_w)
        self.assertEqual(src_w + untouched["left"] + untouched["right"], 1200)
        self.assertEqual(src_h + untouched["top"] + untouched["bottom"], 800)
        # 向内拖不能把框缩到比原图更小，比例仍然成立
        self.assertEqual(src_w + shrunk["left"] + shrunk["right"], 1200)
        self.assertEqual(src_h + shrunk["top"] + shrunk["bottom"], 800)
        self.assertEqual(dragged_left["left"], 600)
        self.assertEqual(1200 + dragged_left["left"] + dragged_left["right"], 1800)
        self.assertEqual(800 + dragged_left["top"] + dragged_left["bottom"], 1200)

    def test_pad_input_keeps_edited_side_and_ratio(self):
        rng = random.Random(20261110)
        cases = []
        for anchor in ("left", "right", "top", "bottom"):
            for _ in range(30):
                ratio = rng.choice(list(RATIOS.values()))
                cases.append([
                    rng.randint(1, 3000), rng.randint(1, 3000),
                    {key: rng.randint(0, 900) for key in ("left", "right", "top", "bottom")},
                    anchor,
                    ratio[0],
                    ratio[1],
                ])
        for value in RATIOS.values():
            cases.append([1200, 800, {"left": 300, "right": 100, "top": 40, "bottom": 60}, "left", value[0], value[1]])
        results = self.run_js(
            "outpaintRatioPadInputPads(c[0], c[1], c[2], c[3], c[4], c[5])",
            cases,
        )
        for case, pads in zip(cases, results):
            anchor = case[3]
            self.assert_ratio_exact(case, pads, (case[4], case[5]))
            # 被编辑的那条边保留用户填的值（除非比例要求它变大）。
            self.assertGreaterEqual(pads[anchor], case[2][anchor], f"{anchor} 输入被缩小: {case} -> {pads}")

    def run_drag(self, preset, mode, dx, dy, shift=False, start=None, natural=(1600, 1200), display=(800, 600)):
        """在 node 里用最小桩子跑真实的 resizeOutpaintFromDrag，验证显示单位→自然像素的换算。"""
        start = start or {"x": 0, "y": 0, "w": display[0], "h": display[1]}
        js = "\n".join([
            f"var OUTPAINT_RATIO_PRESETS = {{'3:2':[3,2],'16:9':[16,9],'1:1':[1,1]}};",
            f"var outpaintRatioPreset = {json.dumps(preset)};",
            "var cropState = " + json.dumps(start) + ";",
            "var cropDrag = {mode:" + json.dumps(mode) + ", sx:0, sy:0, start:" + json.dumps(start) + "};",
            f"var cropBounds = () => ({{w:{display[0]}, h:{display[1]}}});",
            f"var outpaintDisplayScale = () => ({{x:{natural[0] / display[0]}, y:{natural[1] / display[1]}}});",
            "var document = {getElementById: id => (id === 'cropImage' ? {naturalWidth:" + str(natural[0]) + ", naturalHeight:" + str(natural[1]) + "} : null)};",
            "var clampOutpaint = () => { globalThis.clampCalls = (globalThis.clampCalls || 0) + 1; };",
            extract_js_function(self.js_source, "outpaintRatioLockedPads"),
            extract_js_function(self.js_source, "resizeOutpaintFromDrag"),
            f"resizeOutpaintFromDrag({dx}, {dy}, {str(bool(shift)).lower()});",
            "const scale = outpaintDisplayScale();",
            "const state = cropState;",
            "const pads = {left: Math.round(state.x * scale.x), right: Math.round((state.w - state.x - " + str(display[0]) + ") * scale.x),"
            " top: Math.round(state.y * scale.y), bottom: Math.round((state.h - state.y - " + str(display[1]) + ") * scale.y)};",
            "require('fs').writeFileSync(process.argv[2], JSON.stringify({state, pads, clampCalls: globalThis.clampCalls || 0}));",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            js_path = Path(tmp) / "drag.js"
            out_path = Path(tmp) / "out.json"
            js_path.write_text(js, encoding="utf-8")
            subprocess.run(["node", str(js_path), str(out_path)], check=True)
            return json.loads(out_path.read_text(encoding="utf-8"))

    def test_drag_path_keeps_ratio_in_real_units(self):
        # 自然 1600x1200、显示 800x600（缩放 2 倍）：右把手拖 100 显示像素 = 200 自然像素。
        result = self.run_drag("3:2", "outpaint-right", 100, 0)
        pads = result["pads"]
        self.assertEqual(pads, {"left": 0, "right": 200, "top": 0, "bottom": 0})
        total_w, total_h = 1600 + pads["left"] + pads["right"], 1200 + pads["top"] + pads["bottom"]
        self.assertEqual(total_w * 2, total_h * 3)
        self.assertEqual(result["state"]["w"], 900)
        self.assertEqual(result["state"]["h"], 600)
        self.assertEqual(result["clampCalls"], 1)
        # 拖下把手：高度变化，宽度按比例补齐。
        result = self.run_drag("3:2", "outpaint-bottom", 0, 150)
        pads = result["pads"]
        self.assertEqual(pads["top"] + 1200 + pads["bottom"], 1200 + 300)
        self.assertEqual((1600 + pads["left"] + pads["right"]) * 2, (1200 + pads["top"] + pads["bottom"]) * 3)
        # 16:9 时拖右侧，宽度仍按 16:9 补齐。
        result = self.run_drag("16:9", "outpaint-right", 200, 0)
        pads = result["pads"]
        total_w, total_h = 1600 + pads["left"] + pads["right"], 1200 + pads["top"] + pads["bottom"]
        self.assertEqual(total_w * 9, total_h * 16)

    def test_free_preset_keeps_legacy_free_drag(self):
        result = self.run_drag("free", "outpaint-right", 100, 0)
        self.assertEqual(result["state"]["w"], 900)
        self.assertEqual(result["state"]["h"], 600)
        self.assertEqual(result["pads"], {"left": 0, "right": 200, "top": 0, "bottom": 0})

    def test_editor_reapplies_ratio_after_reset_on_entry(self):
        block = self.js_source.split("} else if(imageEditMode === 'outpaint'){", 1)[1].split("}", 1)[0]
        self.assertIn("resetOutpaintBox();", block)
        self.assertIn("applyOutpaintRatioPreset(outpaintRatioPreset);", block)
        self.assertLess(
            block.index("resetOutpaintBox();"),
            block.index("applyOutpaintRatioPreset(outpaintRatioPreset);"),
        )

    def test_drag_and_pad_inputs_are_wired_to_ratio_helpers(self):
        drag_block = extract_js_function(self.js_source, "resizeOutpaintFromDrag")
        self.assertIn("const ratio = OUTPAINT_RATIO_PRESETS[outpaintRatioPreset];", drag_block)
        self.assertIn("outpaintRatioLockedPads(", drag_block)
        self.assertIn("clampOutpaint();", drag_block)
        input_block = extract_js_function(self.js_source, "applyOutpaintPadInputs")
        self.assertIn("outpaintRatioPadInputPads(", input_block)
        self.assertIn("function applyOutpaintPadInputs(anchor)", input_block)
        self.assertIn("['outpaintLeftInput', 'left']", self.js_source)
        self.assertIn("['outpaintBottomInput', 'bottom']", self.js_source)
        self.assertIn("applyOutpaintPadInputs(anchor)", self.js_source)
        # 下拉框继续显示当前比例，不能出现「显示 3:2、实际自由尺寸」
        self.assertIn("ratioSelect.value = OUTPAINT_RATIO_PRESETS[outpaintRatioPreset] ? outpaintRatioPreset : 'free';", self.js_source)


if __name__ == "__main__":
    unittest.main()