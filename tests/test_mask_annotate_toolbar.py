from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MaskAnnotateToolbarTests(unittest.TestCase):
    """「局部编辑」拆分为顶部工具栏独立的「遮罩」「批注」两个入口。"""

    @classmethod
    def setUpClass(cls):
        cls.canvas_html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.canvas_js = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.canvas_css = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        cls.i18n_js = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_toolbar_has_separate_mask_and_annotate_buttons(self):
        mask_btn = self.canvas_html[self.canvas_html.index('data-image-toolbar-action="mask"') - 60:
                                    self.canvas_html.index('data-image-toolbar-action="mask"') + 240]
        brush_btn = self.canvas_html[self.canvas_html.index('data-image-toolbar-action="brush"') - 60:
                                     self.canvas_html.index('data-image-toolbar-action="brush"') + 240]
        self.assertIn("遮罩", mask_btn)
        self.assertIn("canvas.modeMask", mask_btn)
        self.assertIn("批注", brush_btn)
        self.assertIn("canvas.modeAnnotate", brush_btn)
        # 两个入口都只在图片上显示，与其余图片工具一致。
        self.assertIn('data-toolbar-kind="image"', mask_btn)
        self.assertIn('data-toolbar-kind="image"', brush_btn)

    def test_local_edit_entries_removed(self):
        self.assertNotIn('data-image-toolbar-action="local"', self.canvas_html)
        self.assertNotIn('data-image-edit-mode="local"', self.canvas_html)
        self.assertNotIn('id="imageLocalEditModeTools"', self.canvas_html)
        self.assertNotIn('data-local-edit-kind', self.canvas_html)
        self.assertNotIn('id="localEditStatus"', self.canvas_html)

    def test_mode_bar_no_longer_offers_mask_or_brush(self):
        # 遮罩与批注都已迁到独立的全屏编辑器，弹窗模式条不再提供这两个入口。
        self.assertNotIn('data-image-edit-mode="mask"', self.canvas_html)
        self.assertNotIn('data-image-edit-mode="brush"', self.canvas_html)

    def test_js_dispatch_accepts_mask_and_brush_without_local(self):
        # 顶栏「遮罩」「批注」直接打开各自的全屏编辑器，不再进入弹窗的通用分发。
        self.assertIn("if(action === 'mask'){", self.canvas_js)
        self.assertIn("openMaskEditorForImage(node, index);", self.canvas_js)
        self.assertIn("if(action === 'brush'){", self.canvas_js)
        self.assertIn("openAnnotationEditorForImage(node, index);", self.canvas_js)
        # 裁切已迁到独立编辑器，弹窗通用分发只剩扩图与宫格。
        self.assertIn("if(!['outpaint','grid'].includes(action)) return;", self.canvas_js)
        # setImageEditMode 不再有 'local' 伞模式，擦除仍复用内部 mask 模式。
        self.assertIn("imageEditMode = ['preview','outpaint','mask','brush','resize','grid'].includes(mode) ? mode : 'preview';", self.canvas_js)
        self.assertNotIn("mode === 'local'", self.canvas_js)
        self.assertNotIn("localEditKind", self.canvas_js)
        # 弹窗标题仍保留批注词条映射（擦除之外的旧入口复用）。
        self.assertIn("imageEditMode === 'mask' ? 'canvas.modeMask' : imageEditMode === 'brush' ? 'canvas.modeAnnotate'", self.canvas_js)

    def test_erase_still_reuses_mask_mode(self):
        # 「擦除」入口继续复用遮罩编辑 UI，行为保持不变。
        start = self.canvas_js.index("if(action === 'erase'){")
        body = self.canvas_js[start:start + 400]
        self.assertIn("imageEditEraseMode = true;", body)
        self.assertIn("setImageEditMode('mask', true);", body)

    def test_css_toggle_rules_cleaned(self):
        self.assertNotIn(".local-edit-kind-toggle", self.canvas_css)
        self.assertNotIn(".local-edit-mode-tools", self.canvas_css)
        self.assertNotIn(".local-edit-status", self.canvas_css)
        # 遮罩工具行自身的样式保留。
        self.assertIn(".mask-tool-toggle {", self.canvas_css)

    def test_i18n_keys_present(self):
        for key in ('"canvas.modeMask"', '"canvas.modeAnnotate"', '"canvas.localEditMaskStatus"', '"canvas.localEditAnnotateStatus"'):
            self.assertIn(key, self.i18n_js)
        self.assertIn("选择裁剪、遮罩、批注或其他工具", self.i18n_js)


if __name__ == "__main__":
    unittest.main()
