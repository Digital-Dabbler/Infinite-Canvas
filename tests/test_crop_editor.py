"""全屏裁切编辑器的静态契约测试。

覆盖：页面资源接线、编辑器对外契约与比例预设、无 ComfyUI 依赖、
顶栏走独立裁切编辑器、节点显示与下游引用改用裁切结果、
裁切数据模型与遮罩重映射、裁切不改节点尺寸、旧裁剪模式彻底退场，以及 i18n 词条。
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


class CropEditorFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canvas_html = read_text("static/smart-canvas.html")
        cls.canvas_js = read_text("static/js/smart-canvas.js")
        cls.editor_js = read_text("static/js/crop-editor.js")
        cls.editor_css = read_text("static/css/crop-editor.css")
        cls.canvas_css = read_text("static/css/smart-canvas.css")
        cls.i18n_js = read_text("static/js/i18n/smart-canvas.js")

    def _function_body(self, source, signature):
        # 以"下一处顶层函数声明"为边界截取，避免固定字符窗口越过函数尾部、
        # 把相邻函数的代码误判成当前函数的实现（曾因此误报 createImageNodeAt）。
        start = source.index(signature)
        tail = start + len(signature)
        boundaries = [pos for pos in (
            source.find("\nfunction ", tail),
            source.find("\nasync function ", tail),
        ) if pos != -1]
        end = min(boundaries) if boundaries else len(source)
        return source[start:end]

    # ---- 资源接线 ----
    def test_page_loads_editor_assets_in_order(self):
        # 缓存戳由服务端维护，这里只认资源路径与加载顺序。
        self.assertIn('/static/css/crop-editor.css', self.canvas_html)
        self.assertIn('/static/js/crop-editor.js', self.canvas_html)
        # 外壳复用 mask-editor.css，增量样式排在批注之后；脚本必须早于 smart-canvas.js。
        self.assertLess(
            self.canvas_html.index('/static/css/mask-editor.css'),
            self.canvas_html.index('/static/css/crop-editor.css'),
        )
        self.assertLess(
            self.canvas_html.index('/static/css/annotation-editor.css'),
            self.canvas_html.index('/static/css/crop-editor.css'),
        )
        self.assertLess(
            self.canvas_html.index('/static/js/annotation-editor.js'),
            self.canvas_html.index('/static/js/crop-editor.js'),
        )
        self.assertLess(
            self.canvas_html.index('/static/js/crop-editor.js'),
            self.canvas_html.index('/static/js/smart-canvas.js'),
        )

    # ---- 编辑器契约 ----
    def test_editor_public_contract(self):
        # 对外只暴露一个全局：window.CropEditor.open(opts) -> Promise<boolean>
        self.assertIn("window.CropEditor = CropEditor;", self.editor_js)
        self.assertIn("class CropEditorSession", self.editor_js)
        self.assertIn("await this.onSave(result);", self.editor_js)
        self.assertIn("resolve(!!saved);", self.editor_js)
        # 结果是"归一化选框 + 原图像素尺寸 + PNG blob"，调用方据此写 crop 字段。
        for field in ("rect: {", "ratio: this.ratioName,", "width: crop.width,", "height: crop.height,", "blob: blob"):
            self.assertIn(field, self.editor_js)
        # 导出用原图像素，不做降采样。
        self.assertIn("const MIN_CROP_PIXELS = 16;", self.editor_js)

    def test_editor_offers_twelve_ratio_presets(self):
        for name in ("'free'", "'source'", "'1:1'", "'2:3'", "'3:2'", "'3:4'", "'4:3'",
                     "'9:16'", "'16:9'", "'21:9'", "'1:2'", "'2:1'"):
            self.assertIn(name, self.editor_js)
        # 方向键微调、Shift 加速、空格拖动画面。
        for key in ("ArrowLeft", "ArrowUp", "'Space'", "event.shiftKey", "event.altKey"):
            self.assertIn(key, self.editor_js)

    def test_editor_has_no_comfyui_dependencies(self):
        for banned in ("app.registerExtension", "api.fetchApi", "LGraphNode", "app.graph", "require("):
            self.assertNotIn(banned, self.editor_js)

    def test_incremental_css_is_scoped(self):
        # 增量样式只补裁切独有的层，外壳沿用 .me-* 令牌。
        self.assertIn(".ce-", self.editor_css)
        self.assertNotIn(".crop-box", self.editor_css)

    # ---- 顶栏与旧模式退场 ----
    def test_toolbar_crop_opens_fullscreen_editor(self):
        self.assertIn("if(action === 'crop'){", self.canvas_js)
        self.assertIn("openCropEditorForImage(node, index);", self.canvas_js)
        # 弹窗模式条不再提供裁切入口，比例条与旧选框一并删除。
        self.assertNotIn('data-image-edit-mode="crop"', self.canvas_html)
        self.assertNotIn('id="imageCropTools"', self.canvas_html)
        self.assertNotIn('id="cropBox"', self.canvas_html)
        self.assertNotIn("data-crop-ratio", self.canvas_html)

    def test_legacy_crop_code_is_gone(self):
        for legacy in ("function applyImageCrop(", "function resetCropBox(",
                       "function syncCropRatioButtons(", "function setCropAspectPreset(",
                       "cropAspectPreset", "cropAspectRatio", "cropDragModeFromPointer"):
            self.assertNotIn(legacy, self.canvas_js)
        for legacy in (".crop-box {", ".crop-handle {", ".crop-ratio-btn {"):
            self.assertNotIn(legacy, self.canvas_css)

    # ---- 显示与下游 ----
    def test_display_prefers_cropped_result(self):
        display = self._function_body(self.canvas_js, "function imageForDisplay(img){")
        self.assertLess(display.index("imageBaseForDisplay(img)"), display.index("img.crop"))
        self.assertIn("url:displayMediaUrl(crop.result)", display)
        self.assertIn("view.natural_w = width;", display)
        self.assertIn("view.natural_h = height;", display)
        self.assertIn("delete view.annotation;", display)
        self.assertIn("function cropPreviewHtml(img, className='', w=0, h=0){", self.canvas_js)
        self.assertIn("tr('canvas.cropLabel')", self.canvas_js)
        # 缩略图与节点图都要显示裁切结果。
        thumb = self._function_body(self.canvas_js, "function thumbMediaHtml(img, options={}){")
        single = self._function_body(self.canvas_js, "function singleMediaHtml(img, w, h){")
        self.assertIn("imageForDisplay(img)", thumb)
        self.assertIn("imageForDisplay(img)", single)

    def test_downstream_uses_cropped_result(self):
        refs = self._function_body(self.canvas_js, "function promptRequestRefs(refs){")
        self.assertIn("const cropped = Boolean(img.crop?.result?.url);", refs)
        self.assertIn("url:cropped", refs)
        self.assertLess(refs.index("? displayMediaUrl(img.crop.result)"), refs.index(": annotated"))

    # ---- 数据模型与保存 ----
    def test_save_writes_crop_onto_the_same_image(self):
        body = self._function_body(
            self.canvas_js, "async function saveCropEditorResult(node, index, result){")
        self.assertIn("updated.crop = {", body)
        self.assertIn("source:{", body)
        self.assertIn("result:{url:file.url", body)
        self.assertIn("live.images[index] = updated;", body)
        self.assertIn("render(); scheduleSave();", body)
        # 就地生效：保存体里不能新建图片节点。
        self.assertNotIn("createImageNodeAt(", body, "")
        # 裁切不碰节点尺寸：大图一裁就让节点按面积比例膨胀，会把周围节点挤开。
        self.assertNotIn("fitNodeSizeToCrop", body, "")
        self.assertNotIn("node.w =", body, "")

    def test_crop_does_not_resize_node(self):
        body = self._function_body(self.canvas_js, "async function clearImageCrop(node, index){")
        self.assertNotIn("fitNodeSizeToCrop", body, "")
        self.assertNotIn("node.w =", body, "")
        self.assertNotIn("fitNodeSizeToCrop", self.canvas_js)

    def test_crop_can_be_cleared(self):
        body = self._function_body(self.canvas_js, "async function clearImageCrop(node, index){")
        self.assertIn("delete updated.crop;", body)
        self.assertIn("toast(tr('canvas.cropCleared'));", body)

    def test_mask_is_remapped_into_the_new_crop(self):
        body = self._function_body(self.canvas_js, "async function remapMaskForCrop(")
        self.assertIn("imageSmoothingEnabled = false", self.canvas_js)
        self.assertIn("maskBlobHasPaintedArea", body)
        self.assertIn("remapMaskForCrop(item, prevCrop, updated.crop, baseName)", self.canvas_js)
        # 色块图会作为参考图向下传递，裁切范围变化时必须按"新底图 + 搬过来的笔迹"重合成，
        # 否则扩框后会留下透明缺口。
        rebuild = self._function_body(self.canvas_js, "async function rebuildCropColorLayer(")
        self.assertIn("imageBaseForDisplay(item)", rebuild)
        self.assertIn("ctx.globalAlpha", rebuild)
        self.assertIn("rebuildCropColorLayer(item, toCrop, target, paintCanvas)", body)
        # 映射失败要清空遮罩并提示，绝不留下与裁切框错位的遮罩。
        save_start = self.canvas_js.index("async function saveCropEditorResult(node, index, result){")
        save_body = self.canvas_js[save_start:save_start + 4200]
        self.assertIn("delete updated.mask;", save_body)
        self.assertIn("toast(tr('canvas.cropMaskRemapFailed'));", save_body)

    def test_mask_editor_opens_the_cropped_pixels(self):
        # 遮罩活在裁切后的像素空间，编辑底图必须同样是裁切结果。
        self.assertIn("imageUrl: displayMediaUrl(imageForDisplay(item)),", self.canvas_js)

    def test_annotation_stays_on_the_whole_image(self):
        start = self.canvas_js.index("async function openAnnotationEditorForImage(node, index){")
        body = self.canvas_js[start:start + 900]
        self.assertIn("imageUrl: displayMediaUrl(item),", body)
        self.assertIn("cropRect: imageCropRect(item.crop),", body)
        self.assertIn("drawCropScope(ctx)", read_text("static/js/annotation-editor.js"))
        # 批注改完要按原裁切范围重新烘焙裁切图。
        save_start = self.canvas_js.index("async function saveAnnotationEditorResult(node, index, base, result){")
        save_body = self.canvas_js[save_start:save_start + 2600]
        self.assertIn("await refreshCropResultForImage(live, index);", save_body)

    def test_save_as_new_node_drops_derived_state(self):
        body = self._function_body(self.canvas_js, "function saveCropAsNewNode(node, index){")
        self.assertIn("createImageNodeAt(", body)
        self.assertIn("imageForDisplay(item)", body)
        # 新节点是独立成品图：不能再带裁切/遮罩/批注，否则会二次生效。
        self.assertNotIn("crop:", body)
        self.assertNotIn("mask:", body)
        self.assertNotIn("annotation:", body)

    def test_context_menu_exposes_crop_actions(self):
        self.assertIn("data-crop-save-as-node", self.canvas_js)
        self.assertIn("data-crop-clear", self.canvas_js)
        self.assertIn("saveCropAsNewNode(node, imageIndex);", self.canvas_js)
        self.assertIn("clearImageCrop(node, imageIndex);", self.canvas_js)

    def test_download_and_zip_use_cropped_result(self):
        self.assertIn("downloadPreviewFile(imageForDisplay(item) || item);", self.canvas_js)
        group = self._function_body(self.canvas_js, "function previewDownloadGroupItems(){")
        self.assertIn("imageForDisplay(item)", group)
        # ZIP 导出必须换成裁切成品图并摘掉裁切框，否则导入端拿到的是原图 + 包外 URL。
        flatten = self._function_body(self.canvas_js, "function flattenCropForWorkflowExport(node){")
        self.assertIn("delete flat.crop;", flatten)
        self.assertIn("nodes:payload.nodes.map(flattenCropForWorkflowExport)", self.canvas_js)
        self.assertIn(".map(flattenCropForWorkflowExport)", self._function_body(
            self.canvas_js, "function workflowGroupPayload(group){"))

    def test_server_side_send_paths_prefer_cropped_result(self):
        # 发到 Photoshop、复制到系统剪贴板都是"服务端按 node_id + image_index 取图"，
        # 前端传的是裁切图也没用，服务端必须自己也优先用 crop.result。
        source = read_text("main.py")
        for signature in (
            "def photoshop_bridge_source(canvas, node_id, image_index):",
            "def system_clipboard_node_image(canvas, node_id, image_index):",
        ):
            body = self._function_body(source, signature)
            self.assertIn('crop = image.get("crop") if isinstance(image.get("crop"), dict) else {}', body)
            self.assertIn('crop_result = crop.get("result") if isinstance(crop.get("result"), dict) else {}', body)
            self.assertIn("crop_result.get(\"url\"),", body)

    # ---- i18n ----
    def test_i18n_keys_present(self):
        for key in (
            '"canvas.cropLabel"',
            '"canvas.cropSaved"',
            '"canvas.cropCleared"',
            '"canvas.cropSavedAsNode"',
            '"canvas.cropSaveAsNode"',
            '"canvas.cropNoResult"',
            '"canvas.cropMaskRemapFailed"',
            '"canvas.cropEditorTitle"',
            '"canvas.cropEditorHint"',
            '"canvas.cropEditorRatio"',
            '"canvas.cropEditorFree"',
            '"canvas.cropEditorOriginal"',
            '"canvas.cropEditorFit"',
            '"canvas.cropEditorSize"',
            '"canvas.cropEditorClear"',
            '"canvas.cropEditorApply"',
            '"canvas.cropEditorSaving"',
            '"canvas.cropEditorCancel"',
            '"canvas.cropEditorEmptyHint"',
            '"canvas.cropEditorLoadFail"',
            '"canvas.cropEditorSaveFail"',
            '"canvas.cropEditorShortcuts"',
            '"canvas.cropEditorUnavailable"',
            '"canvas.cropEditorScopeHint"',
        ):
            self.assertIn(key, self.i18n_js)
        self.assertIn("裁切编辑器", self.i18n_js)


if __name__ == "__main__":
    unittest.main()