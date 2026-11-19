"""智能画布多层加工（色块+批注/裁切）向下传递修复的静态断言测试。

对应设计文档 docs/superpowers/specs/2026-11-19-smart-canvas-composite-image-transfer-design.md
commit 1：层描述、现场合成器、提交前收口 resolveTransferRefs。
"""
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")


class CompositeTransferTests(unittest.TestCase):
    def test_blob_layer_descriptor_prefers_replayable_paint(self):
        # 色块层描述符：只认 color 模式，优先笔迹层（可重放），透明度钳 0-100。
        self.assertIn("function imageBlobLayerFor(img)", SMART_CANVAS_JS)
        self.assertIn("if(!img || img.maskMode !== 'color') return null;", SMART_CANVAS_JS)
        self.assertIn("const url = paintUrl || colorUrl;", SMART_CANVAS_JS)
        self.assertIn("opacity:Number.isFinite(opacity) ? Math.max(0, Math.min(100, opacity)) : 100", SMART_CANVAS_JS)

    def test_compositor_overlays_layers_in_plan_order(self):
        # commit 3：合成消费统一层规划，layers 自下而上逐层叠加。
        self.assertIn("async function compositeImageEditLayers(transfer)", SMART_CANVAS_JS)
        self.assertIn("const layers = Array.isArray(transfer?.layers) ? transfer.layers : [];", SMART_CANVAS_JS)
        self.assertIn("ctx.globalAlpha = Math.max(0, Math.min(100, Number(transfer.layers[i].opacity) || 100)) / 100;", SMART_CANVAS_JS)
        self.assertIn("canvasEl.toBlob(resolve, 'image/png')", SMART_CANVAS_JS)

    def test_paint_layer_plan_sorts_annotation_above_blob(self):
        # 后画的在上面：无裁切、批注晚于色块且笔迹可重放时，底图退回原图、
        # 批注笔迹盖在色块之上；旧画布无时间戳保持批注在下、色块在上。
        self.assertIn("function imageEditPaintLayers(img)", SMART_CANVAS_JS)
        self.assertIn("if(annPaintUrl && Number.isFinite(annAt) && Number.isFinite(maskAt) && annAt > maskAt){", SMART_CANVAS_JS)
        self.assertIn("return {base:displayMediaUrl({url:localUrl, name:img.name || ''}), layers:[blob, {url:annPaintUrl, opacity:100}]};", SMART_CANVAS_JS)
        self.assertIn("return {base:displayMediaUrl(imageForDisplay(img)), layers:[blob]};", SMART_CANVAS_JS)

    def test_edit_timestamps_written_and_cleaned(self):
        self.assertIn("updated.annotationEditedAt = nowMs();", SMART_CANVAS_JS)
        self.assertIn("delete updated.annotationEditedAt;", SMART_CANVAS_JS)
        self.assertIn("updated.maskEditedAt = nowMs();", SMART_CANVAS_JS)
        self.assertIn("delete updated.maskEditedAt;", SMART_CANVAS_JS)
        # 派生图必须剥掉全部层状态（含色块与时间戳），否则旧笔迹叠到新图上。
        self.assertIn("delete image.annotationEditedAt;", SMART_CANVAS_JS)
        self.assertIn("delete image.maskPaint;", SMART_CANVAS_JS)
        self.assertIn("delete image.maskEditedAt;", SMART_CANVAS_JS)

    def test_prompt_request_refs_attaches_layer_plan_for_transfer(self):
        # 色块参与时挂层规划 transfer；url 保留旧的单层兜底链（合成失败不劣于修复前）。
        self.assertIn("const plan = imageEditPaintLayers(img);", SMART_CANVAS_JS)
        self.assertIn("transfer:plan ? {base:plan.base, layers:plan.layers} : undefined", SMART_CANVAS_JS)
        self.assertIn(": (img.maskMode === 'color' && img.maskColor?.url) ? img.maskColor.url : img.url,", SMART_CANVAS_JS)

    def test_resolve_transfer_refs_is_idempotent_and_falls_back(self):
        self.assertIn("async function resolveTransferRefs(refs)", SMART_CANVAS_JS)
        self.assertIn("if(!list.some(ref => ref?.transfer)) return list;", SMART_CANVAS_JS)
        self.assertIn("const {transfer, ...plain} = ref;", SMART_CANVAS_JS)
        self.assertIn("uploadCroppedBlob(blob, `${baseName}_composite.png`, {temp:true})", SMART_CANVAS_JS)
        # 合成图是本地临时成品：必须清空云端认证素材地址，防止 asset:// 覆盖合成结果。
        self.assertIn("out.push({...plain, url:file.url, asset_uris:{}});", SMART_CANVAS_JS)
        self.assertIn("多层图片合成失败，已回退单层结果传递：", SMART_CANVAS_JS)

    def test_upload_accepts_temp_flag(self):
        self.assertIn("async function uploadCroppedBlob(blob, name, options = {})", SMART_CANVAS_JS)
        self.assertIn("if(options.temp) form.append('temp', '1');", SMART_CANVAS_JS)

    def test_every_generation_entry_resolves_refs(self):
        # 五个提交叶子函数 + 提示词 LLM 视觉输入，全部在消费 ref.url 前收口。
        self.assertIn(
            "async function runApiGeneration(prompt, refs, runSettings=settings, canvasBinding=null){\n"
            "    if(!runSettings.provider_id || !runSettings.model) throw new Error(tr('smart.errNoApiModel'));\n"
            "    refs = await resolveTransferRefs(refs);",
            SMART_CANVAS_JS,
        )
        self.assertIn(
            "async function runRunningHubGeneration(prompt, refs, runSettings=settings){\n"
            "    refs = await resolveTransferRefs(refs);",
            SMART_CANVAS_JS,
        )
        self.assertIn(
            "    try {\n        refs = await resolveTransferRefs(refs);\n"
            "        const uploadedRefs = applyUploadedUrlsToSmartRefs(refs, runSettings);",
            SMART_CANVAS_JS,
        )
        self.assertIn(
            "async function runModelscopeGeneration(prompt, refs, runSettings=settings){\n"
            "    refs = imageRefsOnly(await resolveTransferRefs(refs));",
            SMART_CANVAS_JS,
        )
        self.assertIn(
            "async function runComfyGeneration(node, prompt, refs, pendingNode, meta, runSettings=settings){\n"
            "    const allRefs = await resolveTransferRefs(refs || []);",
            SMART_CANVAS_JS,
        )
        self.assertIn(
            "const mediaRefs = await resolveTransferRefs(promptRequestRefs(promptNodeInputMediaForLLM(node)));",
            SMART_CANVAS_JS,
        )

    def test_mask_side_channel_unchanged(self):
        # 黑白遮罩通道规则保持原样（局部重绘不受本次修复牵连）。
        self.assertIn(
            "mask:(!annotated && img.mask) ? {url:img.mask.url || '', name:img.mask.name || '', kind:img.mask.kind || 'image'} : undefined",
            SMART_CANVAS_JS,
        )

    def test_preview_uses_same_composite_layers_as_transfer(self):
        # commit 2：预览与传递同源——色块参与时统一走叠层预览，
        # 节点主图/翻页缩略图/Composer 输入缩略图三处都调用同一函数。
        self.assertIn("function imageBlobCompositePreviewHtml(img, className='', w=0, h=0)", SMART_CANVAS_JS)
        self.assertIn("if(imageBlobLayerFor(img)) return imageBlobCompositePreviewHtml(img, 'media-thumb');", SMART_CANVAS_JS)
        self.assertIn("if(imageBlobLayerFor(img)) return imageBlobCompositePreviewHtml(img, 'node-img', w, h);", SMART_CANVAS_JS)
        self.assertIn("imageBlobCompositePreviewHtml(img, 'input-thumb-stack')", SMART_CANVAS_JS)
        # 旧的过期色块成品图预览函数已删除，预览不再吃可能过期的 maskColor 整图。
        self.assertNotIn("function maskColorPreviewHtml", SMART_CANVAS_JS)
        # Composer 缩略图签名必须包含层字段，否则只改色块不刷新。
        self.assertIn("@${img.maskPaint?.url || img.maskColor?.url || ''}@${img.maskMode || ''}", SMART_CANVAS_JS)
        css = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        # commit 5：叠层容器内 img 必须清掉占位背景色——透明底笔迹层自带的
        # background:#0f172a 会把下面的底图整个盖住（Composer 黑底 bug 根因）。
        self.assertIn(".input-thumb-stack img { object-fit:contain; background:transparent; }", css)

    def test_loop_node_input_thumbs_follow_same_layer_plan(self):
        # 循环节点顶部的输入参考缩略图也走层规划，兑现"预览怎么显示就怎么传递"。
        self.assertIn(
            "            : (imageBlobLayerFor(img)\n"
            "                ? imageBlobCompositePreviewHtml(img, 'input-thumb-stack')\n"
            "                : smartPreviewImgHtml(img, 256, 'alt=\"\"'));",
            SMART_CANVAS_JS,
        )


if __name__ == "__main__":
    unittest.main()
