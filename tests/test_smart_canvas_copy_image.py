import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
SMART_CANVAS_I18N = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
SMART_CANVAS_HTML = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
SMART_CANVAS_CSS = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")


def extract_function(source, name):
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Unterminated JavaScript function: {name}")


class SmartCanvasCopyImageTests(unittest.TestCase):
    """图片节点右键菜单的「复制图片」必须只出现在图片分支，并且有可用的剪贴板回退。"""

    def setUp(self):
        self.menu = extract_function(SMART_CANVAS_JS, "openPhotoshopContextMenu")

    def test_menu_item_is_offered_for_images_only(self):
        start = self.menu.index("if(kind === 'image'){")
        end = self.menu.index("data-add-my-assets")
        image_branch = self.menu[start:end]

        self.assertIn("data-copy-image", image_branch)
        self.assertIn("tr('smart.copyImage')", image_branch)
        # 视频节点不提供「复制图片」：菜单里只应有一处该入口。
        self.assertEqual(self.menu.count("data-copy-image>"), 1)

    def test_menu_item_copies_the_active_result(self):
        start = self.menu.index("querySelector('[data-copy-image]')")
        handler = self.menu[start:start + 400]

        self.assertIn("event.preventDefault();", handler)
        self.assertIn("event.stopPropagation();", handler)
        self.assertIn("closePhotoshopContextMenu();", handler)
        self.assertIn("copyImageToClipboard(nodeId, imageIndex);", handler)

    def test_clipboard_api_then_server_then_native_guide(self):
        body = extract_function(SMART_CANVAS_JS, "copyImageToClipboard")

        # 写入必须在点击手势内同步发起，否则 await 之后会丢掉用户激活态。
        self.assertIn("const apiAttempt = copyImageWithClipboardApi(url);", body)
        self.assertIn("if(apiAttempt && await apiAttempt){", body)
        self.assertIn("toast(tr('smart.copyImageDone'));", body)
        self.assertIn("const serverResult = await copyImageToSystemClipboard(nodeId, imageIndex);", body)
        self.assertIn("if(serverResult === 'done'){", body)
        self.assertIn("toast(tr('smart.copyImageDoneLocal'));", body)
        self.assertIn("openNativeCopyGuide(nodeId, imageIndex, serverResult);", body)
        # 顺序固定：浏览器剪贴板 API → 服务端本机剪贴板 → 浏览器原生复制引导。
        self.assertLess(body.index("copyImageWithClipboardApi(url)"), body.index("copyImageToSystemClipboard(nodeId, imageIndex)"))
        self.assertLess(body.index("copyImageToSystemClipboard(nodeId, imageIndex)"), body.index("openNativeCopyGuide(nodeId, imageIndex, serverResult)"))
        # 「复制失败」只能出现在拿不到图片地址的早退分支，不能掩盖「必须走原生菜单」的结论。
        self.assertEqual(body.count("toast(tr('smart.copyImageFailed'));"), 1)

    def test_clipboard_write_normalises_to_png(self):
        api_body = extract_function(SMART_CANVAS_JS, "copyImageWithClipboardApi")

        self.assertIn("window.isSecureContext === false", api_body)
        self.assertIn("new ClipboardItem({'image/png': blobPromise})", api_body)
        self.assertIn("navigator.clipboard.write([item])", api_body)

        blob_body = extract_function(SMART_CANVAS_JS, "imageBlobForClipboardCopy")
        self.assertIn("if(blob.type === 'image/png') return blob;", blob_body)
        self.assertIn("return await rasterBlobToPngBlob(blob);", blob_body)

    def test_selection_fallback_restores_the_previous_selection(self):
        body = extract_function(SMART_CANVAS_JS, "copyImageWithSelection")

        self.assertIn("range.selectNode(clone);", body)
        self.assertIn("copied = document.execCommand('copy');", body)
        self.assertIn("holder.remove();", body)
        self.assertIn("savedRanges.forEach(range => selection.addRange(range));", body)

    def test_system_clipboard_response_drives_the_fallback(self):
        body = extract_function(SMART_CANVAS_JS, "copyImageToSystemClipboard")

        self.assertIn("if(!canvasId) return 'failed';", body)
        self.assertIn("fetch('/api/system-clipboard/image'", body)
        self.assertIn("method:'POST'", body)
        self.assertIn("JSON.stringify({canvas_id:canvasId, node_id:nodeId, image_index:imageIndex})", body)
        self.assertIn("if(response.ok) return 'done';", body)
        # 409 = 请求来自局域网其他电脑（服务端只能写运行服务那台电脑的剪贴板）。
        self.assertIn("if(response.status === 409) return 'remote';", body)
        self.assertIn("if(response.status === 501) return 'unavailable';", body)
        self.assertIn("return 'failed';", body)

    def test_native_menu_passthrough_on_modifier_right_click(self):
        start = SMART_CANVAS_JS.index("window.addEventListener('contextmenu'")
        listener = SMART_CANVAS_JS[start:]
        listener = listener[:listener.index("}, true);")]

        self.assertIn("if(!(event.altKey || event.ctrlKey || event.metaKey)) return;", listener)
        # 只放行画布节点自己的图片，避免影响资产库等其他右键菜单。
        self.assertIn("event.target?.closest?.('.thumb-item,.image-wrap')", listener)
        self.assertIn("thumb.closest('.image-node')", listener)
        # 放行 = 阻止应用层的其它处理，但绝不 preventDefault，否则浏览器原生菜单不会弹出。
        self.assertIn("event.stopPropagation();", listener)
        self.assertNotIn("preventDefault", listener)

    def test_native_copy_guide_shows_a_draggable_image(self):
        guide_start = SMART_CANVAS_HTML.index('id="nativeCopyGuide"')
        guide_end = SMART_CANVAS_HTML.index('data-native-copy-link')
        guide_html = SMART_CANVAS_HTML[guide_start:guide_end]

        # 引导层里必须是真实可拖拽的 <img>：浏览器原生「复制图片」和拖进 Photoshop 都依赖它。
        self.assertIn('id="nativeCopyGuideImage"', guide_html)
        self.assertIn('draggable="true"', guide_html)
        self.assertIn("data-native-copy-close", guide_html)
        self.assertIn(".native-copy-guide.open", SMART_CANVAS_CSS)

        body = extract_function(SMART_CANVAS_JS, "openNativeCopyGuide")
        self.assertIn("guideImage.setAttribute('src', url);", body)
        self.assertIn("guide.classList.add('open');", body)
        self.assertIn("guide.setAttribute('aria-hidden', 'false');", body)

    def test_native_guide_link_button_keeps_the_html_link_fallback(self):
        body = extract_function(SMART_CANVAS_JS, "copyNativeGuideImageLink")

        self.assertIn("await loadImageForClipboardCopy(url)", body)
        self.assertIn("copyImageWithSelection(imageElement)", body)
        self.assertIn("toast(tr('smart.copyImageLinkDone'));", body)

        init = extract_function(SMART_CANVAS_JS, "initNativeCopyGuide")
        self.assertIn("data-native-copy-close", init)
        self.assertIn("data-native-copy-link", init)
        self.assertIn("guide.dataset.bound === '1'", init)

    def test_preview_image_supports_native_drag_to_photoshop(self):
        self.assertIn('<img id="previewCurrentImage" class="preview-current" alt="current" draggable="true">', SMART_CANVAS_HTML)

        start = SMART_CANVAS_JS.index("document.getElementById('previewStage').addEventListener('mousedown'")
        handler = SMART_CANVAS_JS[start:]
        handler = handler[:handler.index("previewPanDrag = {")]

        # 原生拖拽要求 mousedown 不被取消，所以按 Ctrl（⌘）时预览舞台必须让出事件。
        self.assertIn("if(event.ctrlKey || event.metaKey) return;", handler)
        self.assertLess(handler.index("if(event.ctrlKey || event.metaKey) return;"), handler.index("event.preventDefault();"))

    def test_i18n_entries_exist_in_both_locales(self):
        for key in (
            "smart.copyImage", "smart.copyImageDone", "smart.copyImageDoneLocal", "smart.copyImageFailed",
            "smart.copyImageNativeTitle", "smart.copyImageNativeHint", "smart.copyImageNativeAltHint",
            "smart.copyImageNativeDragHint", "smart.copyImageNativeLink", "smart.copyImageNativeClose",
            "smart.copyImageLinkDone",
        ):
            match = re.search(rf'"{re.escape(key)}":\s*\{{[^}}]*\}}', SMART_CANVAS_I18N)
            self.assertIsNotNone(match, f"missing i18n entry: {key}")
            entry = match.group(0)
            self.assertIn("zh:", entry, key)
            self.assertIn("en:", entry, key)


if __name__ == "__main__":
    unittest.main()
