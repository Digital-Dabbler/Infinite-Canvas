"""「移除重绘」功能的静态契约测试。

覆盖：顶部工具栏按钮、动作分发、Qwen 移除/局部重绘工作流的参数映射、
遮罩编辑器为这个场景新增的提示词框（颜色叠加模式保持可用），以及节点显示文案。

只读文件做结构断言，不启动服务、不跑 ComfyUI。
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_NAME = "Qwen-2.1-移除、局部重绘_Turbo_api.json"


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


class RemoveRedrawToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canvas_html = read_text("static/smart-canvas.html")
        cls.canvas_js = read_text("static/js/smart-canvas.js")
        cls.editor_js = read_text("static/js/mask-editor.js")
        cls.editor_css = read_text("static/css/mask-editor.css")
        cls.i18n_js = read_text("static/js/i18n/smart-canvas.js")

    def test_toolbar_button(self):
        # 按钮插在「擦除」之后、「放大」之前，沿用同一套 data 属性约定。
        self.assertIn('data-image-toolbar-action="remove-redraw"', self.canvas_html)
        self.assertIn('<span data-i18n="smart.removeRedraw">移除重绘</span>', self.canvas_html)
        erase_at = self.canvas_html.index('data-image-toolbar-action="erase"')
        remove_at = self.canvas_html.index('data-image-toolbar-action="remove-redraw"')
        upscale_at = self.canvas_html.index('data-image-toolbar-action="upscale"')
        self.assertLess(erase_at, remove_at)
        self.assertLess(remove_at, upscale_at)

    def test_action_dispatch(self):
        self.assertIn("if(action === 'remove-redraw'){", self.canvas_js)
        self.assertIn("openRemoveRedrawEditorForImage(node, index);", self.canvas_js)
        self.assertIn("async function openRemoveRedrawEditorForImage(node, index){", self.canvas_js)

    def test_workflow_constant_and_file(self):
        self.assertIn(
            "const SMART_REMOVE_REDRAW_WORKFLOW = 'system/Qwen-2.1-移除、局部重绘_Turbo_api.json';",
            self.canvas_js,
        )
        path = ROOT / "workflows" / "system" / WORKFLOW_NAME
        self.assertTrue(path.is_file(), f"缺少工作流文件：{path}")
        workflow = json.loads(path.read_text(encoding="utf-8"))
        # 遮罩靠 LoadImage 的 alpha 通道传入；提示词用 303；分支由 260→308 决定。
        self.assertEqual(workflow["310"]["class_type"], "LoadImage")
        self.assertEqual(workflow["303"]["class_type"], "PrimitiveStringMultiline")
        self.assertEqual(workflow["303"]["inputs"]["value"], "")
        self.assertEqual(workflow["260"]["inputs"]["Any"], ["303", 0])
        self.assertEqual(workflow["308"]["inputs"]["on_false"], ["212", 0])
        self.assertEqual(workflow["308"]["inputs"]["on_true"], ["252", 0])
        # 288 的输入键是中文「值」，注入时必须同名，否则 ComfyUI 收到未知输入。
        self.assertIn("值", workflow["288"]["inputs"])

    def test_parameter_mapping(self):
        self.assertIn("const SMART_REMOVE_REDRAW_MAX_SIZE = 2560;", self.canvas_js)
        for snippet in (
            "'310': {image: inputName},",
            "'303': {value: String(run.prompt || '')},",
            "'267': {noise_seed: Math.floor(Math.random() * 4294967295)},",
            "'288': {'值': SMART_REMOVE_REDRAW_MAX_SIZE}",
            "workflow_json:SMART_REMOVE_REDRAW_WORKFLOW,",
            "type:'remove-redraw',",
        ):
            self.assertIn(snippet, self.canvas_js)

    def test_prompt_is_trimmed_and_required_mask(self):
        # 空提示词 = 移除（工作流内部走 212 分支），非空 = 局部重绘。
        self.assertIn("const prompt = String(result?.prompt || '').trim();", self.canvas_js)
        self.assertIn("throw new Error(tr('smart.removeRedrawNeedMask'));", self.canvas_js)

    def test_editor_does_not_block_on_generation(self):
        # 点「运行」后编辑页立刻关闭：节点建好后用 void 启动生成，不 await。
        self.assertIn("void finishRemoveRedraw({created, run, sourceRef, maskRef, requestKey});", self.canvas_js)
        self.assertIn("async function finishRemoveRedraw({created, run, sourceRef, maskRef, requestKey}){", self.canvas_js)
        submit = self.canvas_js[
            self.canvas_js.index("async function submitRemoveRedraw("):
            self.canvas_js.index("async function finishRemoveRedraw(")
        ]
        self.assertNotIn("await runQueuedSmartComfyGenerate(", submit)

    def test_result_node_uses_erase_metadata_with_kind(self):
        self.assertIn("function isSmartRemoveRedrawNode(node){", self.canvas_js)
        self.assertIn("node.erase.kind === 'removeRedraw'", self.canvas_js)
        self.assertIn("kind:'removeRedraw'", self.canvas_js)
        # 标题 / 图标 / 失败标题 / 连线标签都要能认出这是移除重绘，而不是普通擦除。
        self.assertIn("isErase ? (isSmartRemoveRedrawNode(node) ? tr('smart.removeRedraw') : tr('smart.erase'))", self.canvas_js)
        self.assertIn("isErase ? (isSmartRemoveRedrawNode(node) ? 'wand-2' : 'eraser')", self.canvas_js)
        self.assertIn(
            "node?.erase ? (isSmartRemoveRedrawNode(node) ? tr('smart.removeRedrawFailedTitle') : tr('smart.eraseFailedTitle'))",
            self.canvas_js,
        )
        self.assertIn(
            "if(isSmartEraseNode(node)) return isSmartRemoveRedrawNode(node) ? tr('smart.removeRedraw') : tr('smart.erase');",
            self.canvas_js,
        )


class MaskEditorRemoveRedrawOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.editor_js = read_text("static/js/mask-editor.js")
        cls.editor_css = read_text("static/css/mask-editor.css")
        cls.canvas_js = read_text("static/js/smart-canvas.js")

    def test_prompt_box_is_opt_in(self):
        # 普通遮罩编辑器没有提示词框，也不该凭空多出 prompt 字段。
        self.assertIn(
            "this.promptBox = this.opts.promptBox && typeof this.opts.promptBox === 'object' ? this.opts.promptBox : null;",
            self.editor_js,
        )
        self.assertIn("const promptMarkup = this.promptBox", self.editor_js)

    def test_color_overlay_mode_stays_available(self):
        # 局部重绘要看「哪块涂了」，颜色叠加模式必须保留：编辑器不再有隐藏它的开关。
        self.assertNotIn("hideColorMode", self.editor_js)
        self.assertNotIn("hideColorMode", self.canvas_js)
        self.assertIn("this.mode = this.opts.mode === 'color' ? 'color' : 'mask';", self.editor_js)
        self.assertIn("const modeSeg =", self.editor_js)
        self.assertIn('data-mode="color"', self.editor_js)
        # 文案也不能被抹掉，否则模式切换按钮是空白。
        self.assertNotIn("delete labels.modeColor;", self.canvas_js)

    def test_prompt_value_returned(self):
        self.assertIn("this.promptInput = root.querySelector('.me-prompt-input');", self.editor_js)
        self.assertIn("prompt: this.promptInput ? String(this.promptInput.value || '').trim() : ''", self.editor_js)

    def test_prompt_box_does_not_capture_painting_or_shortcuts(self):
        # 输入框在舞台内：指针、滚轮、快捷键都不能被画布逻辑吃掉。
        self.assertIn("target.closest('.me-panel, .me-error-box, .me-prompt')", self.editor_js)
        self.assertIn("if (event.target && event.target.closest && event.target.closest('.me-prompt')) return;", self.editor_js)
        self.assertIn("function isEditableTarget(target)", self.editor_js)

    def test_save_failure_shows_reason(self):
        self.assertIn("const message = error && error.message ? String(error.message) : '';", self.editor_js)
        self.assertIn("this.showToast(message || this.label('saveFail'));", self.editor_js)

    def test_prompt_css_exists(self):
        for rule in (".me-prompt {", ".me-prompt-guide {", ".me-prompt-input {"):
            self.assertIn(rule, self.editor_css)
        self.assertIn("transform: translateX(-50%);", self.editor_css)

    def test_labels_override(self):
        self.assertIn("function smartRemoveRedrawLabels(){", self.canvas_js)
        for key in (
            "labels.save = tr('smart.removeRedrawRun');",
            "labels.saving = tr('smart.removeRedrawRunning');",
            "labels.title = tr('smart.removeRedrawEditorTitle');",
        ):
            self.assertIn(key, self.canvas_js)


class RemoveRedrawI18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i18n_js = read_text("static/js/i18n/smart-canvas.js")

    def test_bilingual_entries(self):
        keys = (
            "smart.removeRedraw",
            "smart.removeRedrawEditorTitle",
            "smart.removeRedrawEditorHint",
            "smart.removeRedrawPromptGuide",
            "smart.removeRedrawPromptPlaceholder",
            "smart.removeRedrawRun",
            "smart.removeRedrawRunning",
            "smart.removeRedrawNeedMask",
            "smart.removeRedrawRunningRemove",
            "smart.removeRedrawRunningRedraw",
            "smart.removeRedrawDone",
            "smart.removeRedrawFailed",
            "smart.removeRedrawFailedTitle",
            "smart.removeRedrawHintNode",
            "smart.removeRedrawSubmitFail",
            "smart.removeRedrawLoadFail",
        )
        for key in keys:
            with self.subTest(key=key):
                # 词条里可能含 {error} 占位符，按行定位，别用大括号切分。
                line = next(
                    (ln for ln in self.i18n_js.splitlines() if f'"{key}":' in ln),
                    "",
                )
                self.assertIn("zh:", line)
                self.assertIn("en:", line)

    def test_guide_text_matches_workflow_semantics(self):
        # 说明必须和 303 节点的语义一致：空 = 移除，非空 = 局部重绘。
        self.assertIn("清空提示词为移除；非空提示词为局部重绘", self.i18n_js)


if __name__ == "__main__":
    unittest.main()