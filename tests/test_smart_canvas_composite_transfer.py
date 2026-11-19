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

    def test_compositor_overlays_blob_on_baked_base_same_size(self):
        # 合成 = 同尺寸 alpha 叠加（色块与裁切底图同处裁切像素空间，无需几何反算）。
        self.assertIn("async function compositeImageEditLayers(baseUrl, transfer)", SMART_CANVAS_JS)
        self.assertIn("ctx.globalAlpha = Math.max(0, Math.min(100, Number(transfer.opacity) || 100)) / 100;", SMART_CANVAS_JS)
        self.assertIn("canvasEl.toBlob(resolve, 'image/png')", SMART_CANVAS_JS)

    def test_prompt_request_refs_attaches_transfer_descriptor_for_multilayer(self):
        # 色块与批注/裁切共存时挂 transfer；单层场景不挂，零开销；
        # 兜底 url 仍保留旧的单层链（合成失败时行为不劣于修复前）。
        self.assertIn("const blob = imageBlobLayerFor(img);", SMART_CANVAS_JS)
        self.assertIn("const needsComposite = Boolean(blob && (cropped || annotated));", SMART_CANVAS_JS)
        self.assertIn("transfer:needsComposite ? {blobUrl:blob.url, opacity:blob.opacity} : undefined", SMART_CANVAS_JS)

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


if __name__ == "__main__":
    unittest.main()
