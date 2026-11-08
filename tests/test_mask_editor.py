"""遮罩编辑器迁移（第二步）的静态契约测试。

覆盖：页面接线、编辑器模块的对外契约、颜色叠加参考图接线、i18n 词条，
以及服务端把画布遮罩转换成 OpenAI /images/edits 透明语义的处理。
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


class MaskEditorFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canvas_html = read_text("static/smart-canvas.html")
        cls.canvas_js = read_text("static/js/smart-canvas.js")
        cls.editor_js = read_text("static/js/mask-editor.js")
        cls.editor_css = read_text("static/css/mask-editor.css")
        cls.i18n_js = read_text("static/js/i18n/smart-canvas.js")

    def test_page_loads_editor_assets(self):
        self.assertIn('href="/static/css/mask-editor.css"', self.canvas_html)
        self.assertIn('src="/static/js/mask-editor.js"', self.canvas_html)
        # 编辑器必须先于 smart-canvas.js 加载，打开遮罩时才拿得到 window.MaskEditor。
        self.assertLess(
            self.canvas_html.index('src="/static/js/mask-editor.js"'),
            self.canvas_html.index('src="/static/js/smart-canvas.js'),
        )

    def test_editor_public_contract(self):
        # 对外只暴露一个全局：window.MaskEditor.open(opts) → Promise<boolean>
        self.assertIn("window.MaskEditor = MaskEditor;", self.editor_js)
        self.assertIn("class MaskEditorSession", self.editor_js)
        self.assertIn("await this.onSave(result);", self.editor_js)
        self.assertIn("resolve(!!saved);", self.editor_js)
        for field in (
            "maskBlob: maskBlob,",
            "colorBlob: colorBlob,",
            "mode: this.mode,",
            "colorName: this.colorName,",
            "opacity: this.opacity,",
            "width: this.naturalWidth,",
            "height: this.naturalHeight",
        ):
            self.assertIn(field, self.editor_js)
        # 编辑分辨率与 Goohai 一致；历史改用操作重放，不再存整幅快照。
        self.assertIn("const MAX_EDIT_LONG_SIDE = 1536;", self.editor_js)
        self.assertIn("const MAX_OPS = 200;", self.editor_js)
        self.assertIn("replay()", self.editor_js)
        self.assertNotIn("new ImageData(", self.editor_js)

    def test_editor_has_no_comfyui_dependencies(self):
        # 注释里可以提到参考来源，但代码不许真的依赖 ComfyUI 前端全局。
        for banned in ("app.registerExtension", "api.fetchApi", "LGraphNode", "app.graph", "require("):
            self.assertNotIn(banned, self.editor_js)

    def test_editor_core_features_present(self):
        # 三工具 + 右键调笔刷 + 选框修饰键 + 填洞 + 反转 + 最近邻放大 + 多色层
        for needle in (
            "tool: 'brush'",
            "tool: 'eraser'",
            "tool: 'marquee'",
            "this.rightDrag",
            "beginMarquee(event, pos)",
            "subtract: !!event.altKey",
            "square: !!event.shiftKey",
            "function fillHolesInLayer(",
            "this.inverted = !this.inverted;",
            "imageSmoothingEnabled = false",
            "kind: 'recolor'",
        ):
            self.assertIn(needle, self.editor_js)
        # 保存仍然是不透明黑白 PNG（alpha 恒 255），OpenAI 语义在服务端转换。
        self.assertIn("data[offset + 3] = 255;", self.editor_js)
        self.assertIn(".me-root {", self.editor_css)
        self.assertIn(".me-paint", self.editor_css)

    def test_canvas_wires_editor_and_color_reference(self):
        self.assertIn("if(action === 'mask'){", self.canvas_js)
        self.assertIn("async function openMaskEditorForImage(node, index)", self.canvas_js)
        self.assertIn("async function saveMaskEditorResult(node, index, base, result)", self.canvas_js)
        self.assertIn("updated.maskColor = {url:colorFile.url", self.canvas_js)
        # 颜色叠加图只作为普通参考图追加，不参与 mask_url。
        self.assertIn("mask_color:img.maskColor?.url ?", self.canvas_js)
        # mask_color 只用于追加普通参考图，不能跟随引用对象发给服务端。
        self.assertIn("map(({mask_color, ...ref}) => ({...ref, mask_url:ref.mask?.url || ''}))", self.canvas_js)
        self.assertIn("referenceImages.push({url:colorRef.url", self.canvas_js)
        self.assertIn("referenceImages.push({url:colorRef.url", self.canvas_js)

    def test_i18n_keys_present(self):
        for key in (
            '"canvas.maskEditorTitle"',
            '"canvas.maskEditorSave"',
            '"canvas.maskEditorModeColor"',
            '"canvas.maskEditorColorCyan"',
            '"canvas.maskSaved"',
        ):
            self.assertIn(key, self.i18n_js)


class OpenAiMaskSemanticsTests(unittest.TestCase):
    """服务端遮罩转换：白色涂抹区 → alpha=0，其余不透明。"""

    @classmethod
    def setUpClass(cls):
        # 只解析 main.py 源码，不导入应用（导入会触发启动期写盘）。
        cls.source = read_text("main.py")
        cls.tree = ast.parse(cls.source)
        cls.func = next(
            node for node in cls.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "prepare_openai_mask_file"
        )

    def test_helper_exists_and_is_cached(self):
        self.assertIn("_OPENAI_MASK_CACHE", self.source)
        body = ast.get_source_segment(self.source, self.func)
        self.assertIn("alpha = luma.point(lambda value: 0 if value > 127 else 255)", body)
        self.assertIn("os.makedirs(target_dir, exist_ok=True)", body)
        self.assertIn("return mask_path", body)

    def test_edits_path_converts_mask_before_upload(self):
        start = self.source.index("if mask_refs:\n                    mask_path = output_file_from_url")
        body = self.source[start:start + 800]
        self.assertIn("prepare_openai_mask_file(mask_path)", body)
        # 上传名沿用原遮罩文件名，避免上游按文件名判断。
        self.assertIn("mask_upload_name = os.path.basename(mask_path)", body)
        self.assertLess(body.index("prepare_openai_mask_file(mask_path)"), body.index('files.append(("mask"'))

    def test_conversion_helper_runs_on_real_image(self):
        """真实跑一次转换：白色像素 → 透明，黑色像素 → 不透明。"""
        import os
        import tempfile

        from PIL import Image

        # 直接复用源码里的函数体，不导入整个应用。
        namespace = {}
        imports = "\n".join(
            line for line in self.source.splitlines()
            if line.startswith(("import ", "from ")) and "PIL" in line
        )
        func_src = ast.get_source_segment(self.source, self.func)
        exec(imports, namespace)
        with tempfile.TemporaryDirectory() as tmp_root:
            namespace.update({
                "os": os,
                "hashlib": __import__("hashlib"),
                "OUTPUT_DIR": tmp_root,
                "_OPENAI_MASK_CACHE": {},
            })
            exec(func_src, namespace)
            prepare = namespace["prepare_openai_mask_file"]

            source_path = os.path.join(tmp_root, "canvas_mask.png")
            image = Image.new("RGB", (4, 4), (0, 0, 0))
            for x in range(2):
                for y in range(2):
                    image.putpixel((x, y), (255, 255, 255))
            image.save(source_path)

            converted = prepare(source_path)
            self.assertTrue(os.path.isabs(converted))
            self.assertTrue(converted.startswith(tmp_root))
            with Image.open(converted) as result:
                self.assertEqual(result.mode, "RGBA")
                self.assertEqual(result.getpixel((0, 0))[3], 0)      # 白色涂抹区 → 透明
                self.assertEqual(result.getpixel((3, 3))[3], 255)    # 黑色保留区 → 不透明
                self.assertEqual(result.getpixel((3, 3))[:3], (255, 255, 255))
            # 第二次调用命中缓存，返回同一路径。
            self.assertEqual(prepare(source_path), converted)


if __name__ == "__main__":
    unittest.main()