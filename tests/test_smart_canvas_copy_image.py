import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
SMART_CANVAS_I18N = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")


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

    def test_clipboard_api_runs_before_the_selection_fallback(self):
        body = extract_function(SMART_CANVAS_JS, "copyImageToClipboard")

        self.assertLess(body.index("copyImageWithClipboardApi(url)"), body.index("copyImageWithSelection(imageElement)"))
        # 写入必须在点击手势内同步发起，否则 await 之后会丢掉用户激活态。
        self.assertIn("const apiAttempt = copyImageWithClipboardApi(url);", body)
        self.assertIn("if(apiAttempt && await apiAttempt){", body)
        self.assertIn("toast(tr('smart.copyImageFailed'));", body)
        self.assertIn("toast(tr('smart.copyImageDone'));", body)

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

    def test_i18n_entries_exist_in_both_locales(self):
        for key in ("smart.copyImage", "smart.copyImageDone", "smart.copyImageFailed"):
            match = re.search(rf'"{re.escape(key)}":\s*\{{[^}}]*\}}', SMART_CANVAS_I18N)
            self.assertIsNotNone(match, f"missing i18n entry: {key}")
            entry = match.group(0)
            self.assertIn("zh:", entry, key)
            self.assertIn("en:", entry, key)


if __name__ == "__main__":
    unittest.main()
