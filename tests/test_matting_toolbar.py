from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]

GUIDE_TEXT = "提示词案例“抠出【左上方穿黑色背心的男性】，透明背景”，将【左上方穿黑色背心的男性】替换成自己想要抠出的目标，尽可能的准确描述"


class MattingToolbarTests(unittest.TestCase):
    """图片工具栏“抠图”下拉（去背景 / 指定抠图）与指定抠图生成面板。"""

    @classmethod
    def setUpClass(cls):
        cls.canvas_html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.canvas_js = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.canvas_css = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        cls.i18n_js = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_toolbar_button_renamed_to_matting(self):
        button = self.canvas_html[self.canvas_html.index('data-image-toolbar-action="matting"') - 120:
                                 self.canvas_html.index('data-image-toolbar-action="matting"') + 260]
        self.assertIn("抠图", button)
        # 保留原 class，棋盘图标样式与 canRemoveBackground 隐藏逻辑继续生效。
        self.assertIn("image-toolbar-remove-background", button)

    def test_matting_menu_has_background_first_then_prompt(self):
        menu_start = self.canvas_html.index('id="imageMattingMenu"')
        menu = self.canvas_html[menu_start:self.canvas_html.index("</div>", menu_start + 400)]
        self.assertLess(menu.index('data-matting-mode="background"'), menu.index('data-matting-mode="prompt"'))
        self.assertIn("去背景", menu)
        self.assertIn("指定抠图", menu)

    def test_prompt_panel_guide_and_controls(self):
        panel_start = self.canvas_html.index('id="mattingPromptPanel"')
        panel = self.canvas_html[panel_start:self.canvas_html.index('id="angleControlModal"')]
        self.assertIn(GUIDE_TEXT, panel)
        self.assertIn('id="mattingPromptInput"', panel)
        self.assertIn('id="mattingPromptRun"', panel)
        # 说明在上、输入框和运行按钮在下。
        self.assertLess(panel.index("matting-prompt-guide"), panel.index("mattingPromptInput"))
        self.assertLess(panel.index("mattingPromptInput"), panel.index("mattingPromptRun"))

    def test_main_button_runs_first_item(self):
        start = self.canvas_js.index("if(action === 'matting'){")
        body = self.canvas_js[start:start + 160]
        self.assertIn("runSmartBackgroundRemoval(node, index)", body)
        # 可见性沿用去背景的判断。
        self.assertIn("action === 'matting' && !canRemoveBackground", self.canvas_js)

    def test_menu_prompt_item_opens_panel(self):
        start = self.canvas_js.index('imageMattingMenu?.addEventListener(\'click\'')
        body = self.canvas_js[start:start + 600]
        self.assertIn("option.dataset.mattingMode === 'prompt'", body)
        self.assertIn("openMattingPromptPanel(target)", body)
        self.assertIn("runSmartBackgroundRemoval(target.node, target.index)", body)

    def test_run_button_submits_prompt_matting(self):
        start = self.canvas_js.index("mattingPromptRun?.addEventListener")
        body = self.canvas_js[start:start + 320]
        self.assertIn("runSmartPromptMatting(target.node, target.index", body)
        self.assertIn("mattingPromptInput?.value", body)

    def test_prompt_matting_uses_qwen_edit_workflow(self):
        self.assertIn(
            "const SMART_PROMPT_MATTING_WORKFLOW = 'system/image_qwen_image_2_1_image_edit_api.json';",
            self.canvas_js,
        )
        self.assertTrue((ROOT / "workflows" / "system" / "image_qwen_image_2_1_image_edit_api.json").exists())
        start = self.canvas_js.index("async function runSmartPromptMatting")
        body = self.canvas_js[start:start + 4200]
        # LoadImage 节点 470 注入图片，TextEncodeQwenImage21 节点 510 注入提示词。
        self.assertIn("params:{'470':{image:inputName}, '510':{prompt}}", body)
        self.assertIn("type:'matting'", body)
        self.assertIn("workflow_json:SMART_PROMPT_MATTING_WORKFLOW", body)
        # 空提示词必须有拦截提示。
        self.assertIn("smart.mattingNeedPrompt", body)

    def test_panel_follows_toolbar_and_closes(self):
        start = self.canvas_js.index("function positionMattingPromptPanel")
        body = self.canvas_js[start:start + 1400]
        self.assertIn("closeMattingPromptPanel()", body)
        # Escape 关闭菜单与面板。
        self.assertIn("closeSmartCanvasShortcuts(); imageMattingMenuClose(); closeMattingPromptPanel(); }", self.canvas_js)

    def test_panel_replaces_composer(self):
        # 打开面板时隐藏原生成面板（composer），关闭时恢复。
        start = self.canvas_js.index("function openMattingPromptPanel")
        body = self.canvas_js[start:start + 500]
        self.assertIn("composer?.classList.add('matting-hidden')", body)
        close_start = self.canvas_js.index("function closeMattingPromptPanel")
        close_body = self.canvas_js[close_start:close_start + 400]
        self.assertIn("composer?.classList.remove('matting-hidden')", close_body)
        # 面板与 composer 同宽同位（540px、节点下方 gap 14）。
        self.assertIn("width:min(540px", self.canvas_css)
        self.assertIn(".composer.matting-hidden {", self.canvas_css)
        self.assertIn("const gap = 14;", self.canvas_js[self.canvas_js.index("function positionMattingPromptPanel"):])

    def test_result_node_title_is_matting(self):
        # 结果节点标题从「图片生成」改为「指定抠图」：标题链、连接标签与图标均走 isSmartPromptMattingNode。
        self.assertIn("function isSmartPromptMattingNode(node){", self.canvas_js)
        self.assertIn("isSmartPromptMattingNode(node) ? tr('smart.mattingTargeted')", self.canvas_js)
        self.assertIn("if(isSmartPromptMattingNode(node)) return tr('smart.mattingTargeted');", self.canvas_js)
        self.assertIn("isSmartPromptMattingNode(node) ? 'wand-sparkles'", self.canvas_js)

    def test_result_node_rerun_carries_prompt_and_source_image(self):
        start = self.canvas_js.index("async function runSmartPromptMatting")
        body = self.canvas_js[start:start + 4600]
        # 重跑时生成面板预填提示词并带上原图（manualInputRefs 进入 comfy 自定义工作流 refs）。
        self.assertIn("created.runPrompt = prompt;", body)
        self.assertIn("created.manualInputRefs = [{...sourceRef}];", body)

    def test_matting_workflow_config_maps_panel_fields(self):
        config_path = ROOT / "workflows" / "system" / "image_qwen_image_2_1_image_edit_api.config.json"
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        workflow = json.loads((ROOT / "workflows" / "system" / "image_qwen_image_2_1_image_edit_api.json").read_text(encoding="utf-8"))
        fields = {field["id"]: field for field in config["fields"]}
        self.assertEqual(fields["source_image"]["type"], "image")
        self.assertEqual(fields["matting_prompt"]["type"], "prompt")
        for field_id in ("source_image", "matting_prompt"):
            field = fields[field_id]
            node = workflow.get(field["node"])
            self.assertIsNotNone(node, f"workflow node {field['node']} missing")
            self.assertIn(field["input"], node.get("inputs", []), f"{field_id} input not declared on node")
        self.assertIn("470", workflow)
        self.assertIn("510", workflow)

    def test_css_panel_and_i18n_keys(self):
        self.assertIn(".matting-prompt-panel.open { display:flex; }", self.canvas_css)
        self.assertIn(".image-matting-menu .background-removal-mark", self.canvas_css)
        for key in ("smart.matting\"", "smart.mattingMode\"", "smart.mattingTargeted\"",
                    "smart.mattingGuide\"", "smart.mattingPromptPlaceholder\"", "smart.mattingRun\"",
                    "smart.mattingNeedPrompt\""):
            self.assertIn(key, self.i18n_js)
        self.assertIn(GUIDE_TEXT, self.i18n_js)


if __name__ == "__main__":
    unittest.main()
