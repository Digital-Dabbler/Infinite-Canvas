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


class SmartCanvasPreviewCopyPromptTests(unittest.TestCase):
    """图片预览底部的「复制提示词」必须复制当前图片真实使用的提示词。"""

    def test_copy_button_lives_in_the_prompt_card_before_recreate(self):
        prompt_start = SMART_CANVAS_HTML.index('class="preview-generation-prompt"')
        panel_end = SMART_CANVAS_HTML.index('id="previewReproduceBtn"')
        prompt_card = SMART_CANVAS_HTML[prompt_start:panel_end]

        self.assertIn('id="previewPromptCopyBtn"', prompt_card)
        self.assertIn("onclick=\"copyPreviewGenerationPrompt()\"", prompt_card)
        # 默认禁用，等面板解析出该图片的提示词后再启用。
        button_tag = re.search(r'<button id="previewPromptCopyBtn"[^>]*>', prompt_card)
        self.assertIsNotNone(button_tag)
        self.assertIn("disabled", button_tag.group(0))
        # 不覆盖面板的提示词正文：复制按钮是提示词卡内的独立控件。
        self.assertIn('id="previewGenerationPrompt"', prompt_card)
        self.assertLess(prompt_card.index('id="previewGenerationPrompt"'), prompt_card.index('id="previewPromptCopyBtn"'))

    def test_copy_uses_the_prompt_of_the_previewed_output(self):
        body = extract_function(SMART_CANVAS_JS, "copyPreviewGenerationPrompt")

        self.assertIn("const editing = currentEditImage();", body)
        # 必须与面板展示一致：按当前预览图匹配生成历史，而不是节点最后一次运行。
        self.assertIn("generationRunValuesFor(editing.node, editing.image).prompt", body)
        self.assertIn("await copyTextToClipboard(prompt)", body)
        self.assertIn("toast(tr('smart.copyFailed'));", body)
        self.assertIn("toast(tr('smart.copyEmpty'));", body)
        self.assertNotIn("runPrompt", body)

    def test_empty_prompt_disables_the_button(self):
        body = extract_function(SMART_CANVAS_JS, "updatePreviewGenerationPanel")

        self.assertIn("const copyBtn = document.getElementById('previewPromptCopyBtn');", body)
        self.assertIn("copyBtn.disabled = !prompt;", body)
        self.assertIn("copyBtn.title = copyBtn.disabled ? tr('smart.previewEmptyPrompt') : tr('smart.previewCopyPrompt');", body)

    def test_copied_state_swaps_the_label_and_resets(self):
        body = extract_function(SMART_CANVAS_JS, "setPreviewPromptCopyState")

        self.assertIn("classList.toggle('is-copied'", body)
        self.assertIn("clearTimeout(copyBtn._copyResetTimer);", body)
        self.assertIn("setTimeout(() => setPreviewPromptCopyState(false), 1600)", body)

        label_body = extract_function(SMART_CANVAS_JS, "syncPreviewPromptCopyLabel")
        self.assertIn("copied ? tr('smart.copied') : tr('smart.previewCopyPrompt')", label_body)
        self.assertIn("refreshIcons();", label_body)

    def test_styles_exist_for_the_default_and_copied_states(self):
        self.assertIn(".preview-prompt-copy-btn {", SMART_CANVAS_CSS)
        self.assertIn(".preview-prompt-copy-btn:disabled", SMART_CANVAS_CSS)
        self.assertIn(".preview-prompt-copy-btn.is-copied", SMART_CANVAS_CSS)
        self.assertIn(".preview-generation-prompt-body {", SMART_CANVAS_CSS)

    def test_i18n_entries_exist_in_both_locales(self):
        for key in ("smart.previewCopyPrompt", "smart.previewPromptCopied"):
            match = re.search(rf'"{re.escape(key)}":\s*\{{[^}}]*\}}', SMART_CANVAS_I18N)
            self.assertIsNotNone(match, f"missing i18n entry: {key}")
            entry = match.group(0)
            self.assertIn("zh:", entry, key)
            self.assertIn("en:", entry, key)


if __name__ == "__main__":
    unittest.main()