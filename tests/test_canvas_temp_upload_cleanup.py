"""C+ 方案 commit 4：临时合成图服务端标记/清扫 + 导出与另存拉平的综合结果。

与仓库既有测试一致，采用对源码文本的静态断言（不导入 main.py，避免启动副作用）。
"""
import pathlib
import unittest


class CanvasTempUploadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.main_py = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")

    def test_upload_endpoint_accepts_temp_flag(self):
        self.assertIn(
            'async def upload_ai_reference(files: List[UploadFile] = File(...), temp: str = Form("")):',
            self.main_py,
        )
        self.assertIn('prefix = "tmp_ai_ref_" if str(temp or "").strip() == "1" else "ai_ref_"', self.main_py)
        self.assertIn('filename = f"{prefix}{uuid.uuid4().hex[:12]}{ext}"', self.main_py)

    def test_temp_media_sweep_with_module_level_threshold(self):
        self.assertIn("CANVAS_TEMP_MEDIA_MAX_AGE_SECONDS = 24 * 60 * 60", self.main_py)
        self.assertIn("def cleanup_canvas_temp_media(max_age_seconds=CANVAS_TEMP_MEDIA_MAX_AGE_SECONDS):", self.main_py)
        self.assertIn('if not str(name).startswith("tmp_ai_ref_"):', self.main_py)
        self.assertIn("if now_ts - os.path.getmtime(path) > max_age_seconds:", self.main_py)

    def test_sweep_runs_at_startup_and_periodically(self):
        self.assertIn("await asyncio.to_thread(cleanup_canvas_temp_media)", self.main_py)
        self.assertIn("asyncio.create_task(canvas_temp_media_cleanup_loop())", self.main_py)
        self.assertIn("async def canvas_temp_media_cleanup_loop():", self.main_py)


class CompositeFlattenConsumersTests(unittest.TestCase):
    def setUp(self):
        self.js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_layer_field_stripping_helper_exists(self):
        self.assertIn("function clearImageEditLayerFields(item){", self.js)
        self.assertIn("delete item.maskEditedAt;", self.js)
        self.assertIn("delete item.annotationEditedAt;", self.js)

    def test_workflow_export_flattens_composite(self):
        # ZIP/工作流导出：色块与裁切/批注共存时先合成成品再导出，且剥掉层字段。
        self.assertIn("async function flattenCropForWorkflowExport(node){", self.js)
        self.assertIn("if(plan && (cropped || annotated)){", self.js)
        self.assertIn("clearImageEditLayerFields(flat);", self.js)
        self.assertIn("nodes:await Promise.all(payload.nodes.map(flattenCropForWorkflowExport)),", self.js)
        self.assertIn("nodes:await Promise.all(members.map(n=>{", self.js)
        self.assertIn("body:JSON.stringify({...await workflowGroupPayload(group),name", self.js)

    def test_save_crop_as_new_node_uses_composite(self):
        # 裁切另存新节点：色块参与时保存综合结果，与向下传递一致。
        self.assertIn("async function saveCropAsNewNode(node, index){", self.js)
        self.assertIn("const plan = imageEditPaintLayers(item);", self.js)
        self.assertIn("await saveCropAsNewNode(node, imageIndex);", self.js)

    def test_temp_upload_flag_is_sent_by_transfer_compositor(self):
        # 提交前合成走临时上传（temp=1），导出/另存走正式上传（无 temp）。
        self.assertIn("if(options.temp) form.append('temp', '1');", self.js)
        self.assertIn("uploadCroppedBlob(blob, `${baseName}_composite.png`, {temp:true})", self.js)


if __name__ == "__main__":
    unittest.main()
