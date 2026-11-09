"""全屏批注编辑器的静态契约测试。

覆盖：页面资源接线、编辑器对外契约与工具集、Ctrl+滚轮调整工具大小、
保存后就地写回原图片条目（不新建节点）、节点预览与下游引用改用批注图、
以及 i18n 词条是否齐全。
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


class AnnotationEditorFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canvas_html = read_text("static/smart-canvas.html")
        cls.canvas_js = read_text("static/js/smart-canvas.js")
        cls.editor_js = read_text("static/js/annotation-editor.js")
        cls.editor_css = read_text("static/css/annotation-editor.css")
        cls.mask_editor_js = read_text("static/js/mask-editor.js")
        cls.i18n_js = read_text("static/js/i18n/smart-canvas.js")

    def test_page_loads_editor_assets_in_order(self):
        # 缓存戳由服务端维护，这里只认资源路径与加载顺序。
        self.assertIn('/static/css/annotation-editor.css', self.canvas_html)
        self.assertIn('/static/js/annotation-editor.js', self.canvas_html)
        # 外壳复用 mask-editor.css，增量样式在其后；脚本必须早于 smart-canvas.js。
        self.assertLess(
            self.canvas_html.index('/static/css/mask-editor.css'),
            self.canvas_html.index('/static/css/annotation-editor.css'),
        )
        self.assertLess(
            self.canvas_html.index('/static/js/mask-editor.js'),
            self.canvas_html.index('/static/js/annotation-editor.js'),
        )
        self.assertLess(
            self.canvas_html.index('/static/js/annotation-editor.js'),
            self.canvas_html.index('/static/js/smart-canvas.js'),
        )

    def test_editor_public_contract(self):
        # 对外只暴露一个全局：window.AnnotationEditor.open(opts) → Promise<boolean>
        self.assertIn("window.AnnotationEditor = AnnotationEditor;", self.editor_js)
        self.assertIn("class AnnotationEditorSession", self.editor_js)
        self.assertIn("await this.onSave(result);", self.editor_js)
        self.assertIn("resolve(!!saved);", self.editor_js)
        for field in ("flattenBlob: flattenBlob,", "overlayBlob: overlayBlob,", "hasMarks: hasMarks,"):
            self.assertIn(field, self.editor_js)
        # 撤销走矢量操作重放，不保存整幅 ImageData 快照。
        self.assertIn("const MAX_OPS = 200;", self.editor_js)
        self.assertIn("replayInto(ctx) {", self.editor_js)
        self.assertNotIn("new ImageData(", self.editor_js)

    def test_editor_offers_six_annotation_tools(self):
        for tool in ("brush", "eraser", "rect", "ellipse", "label", "text"):
            self.assertIn("{ tool: '%s'," % tool, self.editor_js)
        # 编号工具沿用画布旧批注的圈号。
        self.assertIn("circledNumber", self.editor_js)
        # 文字批注可拖动、拖角缩放、双击改字。
        self.assertIn("el.className = 'ae-text-editor';", self.editor_js)
        self.assertIn("startTextMove(event, point)", self.editor_js)
        self.assertIn("startTextResize(event, point)", self.editor_js)

    def test_ctrl_wheel_resizes_instead_of_zooming(self):
        wheel = self.editor_js[self.editor_js.index("handleWheel(event) {"):
                              self.editor_js.index("handleWheel(event) {") + 700]
        self.assertIn("event.ctrlKey || event.metaKey", wheel)
        self.assertIn("this.adjustBrushSize(step);", wheel)
        self.assertIn("this.zoomAt(", wheel)
        # 遮罩编辑器同样支持 Ctrl+滚轮调画笔大小，普通滚轮仍是缩放。
        mask_wheel = self.mask_editor_js[self.mask_editor_js.index("handleWheel(event) {"):
                                        self.mask_editor_js.index("handleWheel(event) {") + 700]
        self.assertIn("event.ctrlKey || event.metaKey", mask_wheel)
        self.assertIn("this.adjustBrushSize(", mask_wheel)
        self.assertIn("this.zoomAt(", mask_wheel)

    def test_editor_has_no_comfyui_dependencies(self):
        for banned in ("app.registerExtension", "api.fetchApi", "LGraphNode", "app.graph", "require("):
            self.assertNotIn(banned, self.editor_js)

    def test_toolbar_brush_opens_fullscreen_editor(self):
        self.assertIn("if(action === 'brush'){", self.canvas_js)
        self.assertIn("openAnnotationEditorForImage(node, index);", self.canvas_js)
        # 弹窗模式条不再提供批注入口。
        self.assertNotIn('data-image-edit-mode="brush"', self.canvas_html)
        # 旧绘制画布仍被「擦除/遮罩」复用，工具行保留。
        self.assertIn('id="imageBrushTools"', self.canvas_html)

    def test_save_writes_back_onto_the_same_image(self):
        start = self.canvas_js.index("async function saveAnnotationEditorResult(node, index, base, result){")
        body = self.canvas_js[start:start + 2200]
        # 住回原图片条目：批注结果 + 可继续编辑的透明底稿。
        self.assertIn("updated.annotation = {url:flattenFile.url", body)
        self.assertIn("updated.annotationPaint = {url:paintFile.url", body)
        self.assertIn("delete updated.annotation;", body)
        self.assertIn("delete updated.annotationPaint;", body)
        self.assertIn("live.images[index] = updated;", body)
        self.assertIn("render(); scheduleSave();", body)
        self.assertIn("uploadCroppedBlob(result.flattenBlob, `${base}_annotate.png`)", body)
        self.assertIn("uploadCroppedBlob(result.overlayBlob, `${base}_annotatepaint.png`)", body)
        # 与遮罩一样就地生效，保存体里不能新建图片节点。
        self.assertNotIn("createImageNodeAt(", body)

    def test_editor_reopens_with_existing_overlay(self):
        self.assertIn("overlayUrl: item.annotationPaint?.url ? displayMediaUrl(item.annotationPaint) : '',", self.canvas_js)
        self.assertIn("this.baseline = image;", self.editor_js)

    def _function_body(self, source, signature):
        start = source.index(signature)
        end = source.find("\nfunction ", start + len(signature))
        return source[start:end if end != -1 else len(source)]

    def test_display_uses_annotation_image(self):
        # 节点图与缩略图都要显示批注结果，且批注优先于遮罩（裁切结果已在批注之上烘焙）。
        thumb = self._function_body(self.canvas_js, "function thumbMediaHtml(img, options={}){")
        self.assertIn("if(!cropped && view?.annotation?.url) return annotationPreviewHtml(view, 'media-thumb');", thumb)
        single = self._function_body(self.canvas_js, "function singleMediaHtml(img, w, h){")
        self.assertIn("if(!cropped && view?.annotation?.url) return annotationPreviewHtml(view, 'node-img', w, h);", single)
        self.assertIn("function annotationPreviewHtml(img, className='', w=0, h=0){", self.canvas_js)
        self.assertIn("tr('canvas.annotationLabel')", self.canvas_js)
        # 批注分支必须排在遮罩分支之前。
        self.assertLess(
            thumb.index("view?.annotation?.url"),
            thumb.index("if(view?.mask?.url)"),
        )
        # 预览、放大、抠图、裁剪等统一经 imageForDisplay 取到批注后的图；
        # 批注本身的烘焙收口在 imageBaseForDisplay。
        display = self._function_body(self.canvas_js, "function imageForDisplay(img){")
        self.assertIn("const base = imageBaseForDisplay(img);", display)
        base_display = self._function_body(self.canvas_js, "function imageBaseForDisplay(img){")
        self.assertIn("if(img.annotation?.url){", base_display)
        self.assertIn("url:displayMediaUrl(img.annotation)", base_display)

    def test_downstream_uses_annotation_image_without_mask(self):
        refs = self._function_body(self.canvas_js, "function promptRequestRefs(refs){")
        # 裁切结果（已烘焙批注）优先级最高，其余仍以批注图优先。
        self.assertIn("url:cropped", refs)
        self.assertIn("? displayMediaUrl(img.crop.result)", refs)
        self.assertIn(": annotated", refs)
        self.assertIn("? displayMediaUrl(img.annotation)", refs)
        self.assertIn("mask:(!annotated && img.mask)", refs)

    def test_derived_images_do_not_inherit_annotation(self):
        replace_body = self._function_body(self.canvas_js, "function replaceEditedImage(file, extra={}){")
        self.assertIn("delete image.annotation;", replace_body)
        self.assertIn("delete image.annotationPaint;", replace_body)
        # 保存画布时会经过 stripImageGenerationMeta：绝不能在这里删除批注字段。
        strip_body = self._function_body(self.canvas_js, "function stripImageGenerationMeta(img){")
        self.assertNotIn("annotation", strip_body)

    def test_i18n_keys_present(self):
        for key in (
            '"canvas.annotationLabel"',
            '"canvas.annotationEditorTitle"',
            '"canvas.annotationEditorToolBrush"',
            '"canvas.annotationEditorToolEraser"',
            '"canvas.annotationEditorToolRect"',
            '"canvas.annotationEditorToolEllipse"',
            '"canvas.annotationEditorToolLabel"',
            '"canvas.annotationEditorToolText"',
            '"canvas.annotationEditorSizeText"',
            '"canvas.annotationEditorHint"',
            '"canvas.annotationEditorShortcuts"',
            '"canvas.annotationEditorSave"',
            '"canvas.annotationEditorApplying"',
            '"canvas.annotationEditorEmptyHint"',
            '"canvas.annotationEditorLoadFail"',
            '"canvas.annotationEditorSaveFail"',
            '"canvas.annotationEditorUnavailable"',
            '"canvas.annotationEditorTextPlaceholder"',
            '"canvas.annotationEditorPanelNote"',
            '"canvas.annotationEditorApplied"',
            '"canvas.annotationEditorRemoved"',
        ):
            self.assertIn(key, self.i18n_js)
        self.assertIn("批注编辑器", self.i18n_js)

    def test_incremental_css_is_scoped(self):
        # 增量样式只补批注独有的层，不重复定义遮罩外壳。
        self.assertIn(".ae-text-editor {", self.editor_css)
        self.assertIn(".ae-panel-note {", self.editor_css)


if __name__ == "__main__":
    unittest.main()