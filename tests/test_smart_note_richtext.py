"""Smart note rich text (PureRef-style Note) contracts.

The client sanitiser (``sanitizeNoteRichText()`` in ``static/js/smart-canvas.js``) and
the server sanitiser (``sanitize_note_richtext()`` in ``main.py``) guard the same
payload: the one HTML string in a canvas node that is rendered for real.  Both are
driven from one shared corpus so that a rule change on either side shows up as a
failing case instead of as stored HTML the other side silently rewrites.
"""

import ast
import functools
import html
import json
import re
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
SMART_CANVAS_CSS = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
SMART_CANVAS_I18N = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
MAIN_PY = (ROOT / "main.py").read_text(encoding="utf-8")
CASES_PATH = ROOT / "tests" / "fixtures" / "smart-note-richtext-cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def extract_function(name: str, source: str = SMART_CANVAS_JS) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    if source[:start].endswith("async "):
        start -= len("async ")
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


CLIENT_CONSTANTS = SMART_CANVAS_JS[
    SMART_CANVAS_JS.index("const NOTE_RICHTEXT_MAX"):SMART_CANVAS_JS.index("function noteRichTextSafeLink(")
]
CLIENT_FUNCTIONS = "\n".join(
    extract_function(name)
    for name in (
        "noteRichTextSafeLink",
        "noteRichTextCheckedValue",
        "noteRichTextSizeValue",
        "noteRichTextAlignValue",
        "noteRichTextAttrText",
        "noteRichTextDecodeEntities",
        "noteRichTextTagAllowed",
        "noteRichTextAttributesFor",
        "sanitizeNoteRichText",
    )
)

NODE_RUNNER = """
const fs = require('fs');
const payload = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const results = payload.cases.map(item => sanitizeNoteRichText(item.input));
process.stdout.write(JSON.stringify(results));
"""

SERVER_ASSIGNMENTS = {
    "NOTE_RICHTEXT_MAX",
    "NOTE_RICHTEXT_TAGS",
    "NOTE_RICHTEXT_ALIASES",
    "NOTE_RICHTEXT_BLOCK_TAGS",
    "NOTE_RICHTEXT_SIZES",
    "NOTE_RICHTEXT_ALIGNMENTS",
    "NOTE_RICHTEXT_DROP_TAGS",
    "CANVAS_NODE_OPERATION_FORBIDDEN_FIELDS",
}
SERVER_DEFINITIONS = {
    "note_richtext_safe_link",
    "note_richtext_checked_value",
    "note_richtext_size_value",
    "note_richtext_align_value",
    "NoteRichTextSanitizer",
    "sanitize_note_richtext",
    "validate_canvas_node_fields",
}


class StubHTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def load_server_namespace() -> dict:
    """Exec just the sanitiser and node-field validation out of main.py.

    Importing ``main`` would start the app; the server rules under test are plain
    functions, so their source is lifted with ``ast`` and run in a bare namespace.
    """
    tree = ast.parse(MAIN_PY)
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in SERVER_DEFINITIONS:
            chunks.append(ast.get_source_segment(MAIN_PY, node))
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in SERVER_ASSIGNMENTS for target in node.targets
        ):
            chunks.append(ast.get_source_segment(MAIN_PY, node))
    found = len(chunks)
    if found != len(SERVER_ASSIGNMENTS) + len(SERVER_DEFINITIONS):
        raise AssertionError(f"main.py sanitiser source was not fully extracted ({found} chunks)")
    namespace = {
        "re": re,
        "html": html,
        "HTMLParser": HTMLParser,
        "List": list,
        "Dict": dict,
        "HTTPException": StubHTTPException,
        "validate_director_scene": lambda value: value,
    }
    exec("\n\n".join(chunks), namespace)  # noqa: S102 - extracted, reviewed source
    return namespace


def run_client_sanitiser() -> list:
    """Run the shared corpus through the browser sanitiser (cached: node startup is slow)."""
    return list(_run_client_sanitiser())


@functools.lru_cache(maxsize=1)
def _run_client_sanitiser() -> tuple:
    completed = subprocess.run(
        ["node", "-e", CLIENT_CONSTANTS + "\n" + CLIENT_FUNCTIONS + "\n" + NODE_RUNNER, str(CASES_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return tuple(json.loads(completed.stdout))


class SmartNoteRichTextCaseTests(unittest.TestCase):
    def test_client_sanitiser_matches_the_shared_corpus(self):
        results = run_client_sanitiser()
        self.assertEqual(len(results), len(CASES))
        for case, actual in zip(CASES, results):
            with self.subTest(case=case["name"]):
                self.assertEqual(case["expected"], actual)

    def test_server_sanitiser_matches_the_shared_corpus(self):
        sanitize = load_server_namespace()["sanitize_note_richtext"]
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertEqual(case["expected"], sanitize(case["input"]))

    def test_both_sanitisers_agree_with_each_other(self):
        server = load_server_namespace()["sanitize_note_richtext"]
        for case, client_result in zip(CASES, run_client_sanitiser()):
            with self.subTest(case=case["name"]):
                self.assertEqual(server(case["input"]), client_result)


class SmartNoteNodeFieldTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_server_namespace()
        self.validate = self.namespace["validate_canvas_node_fields"]

    def assert_rejected(self, node, fields, detail):
        with self.assertRaises(StubHTTPException) as caught:
            self.validate(node, fields)
        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual(detail, caught.exception.detail)

    def test_rich_text_is_sanitised_for_notes(self):
        clean = self.validate({"type": "smart-note"}, {"richText": "<b>x</b><script>alert(1)</script>"})
        self.assertEqual("<strong>x</strong>", clean["richText"])

    def test_rich_text_rejects_other_node_types(self):
        self.assert_rejected({"type": "smart-3d-director"}, {"richText": "<b>x</b>"}, "只有便签节点可更新富文本")

    def test_rich_text_rejects_non_strings_and_oversized_payloads(self):
        self.assert_rejected({"type": "smart-note"}, {"richText": 42}, "便签富文本无效")
        oversized = "<h1 data-fs=\"1\">" + ("x" * 20000) + "</h1>"
        self.assert_rejected({"type": "smart-note"}, {"richText": oversized}, "便签富文本无效")

    def test_size_mode_only_accepts_the_two_literals(self):
        self.assertEqual("fixed", self.validate({"type": "smart-note"}, {"sizeMode": "fixed"})["sizeMode"])
        self.assert_rejected({"type": "smart-note"}, {"sizeMode": "huge"}, "便签尺寸模式无效")

    def test_background_alpha_is_clamped(self):
        self.assertEqual(0, self.validate({"type": "smart-note"}, {"bgAlpha": -40})["bgAlpha"])
        self.assertEqual(100, self.validate({"type": "smart-note"}, {"bgAlpha": 400})["bgAlpha"])
        self.assertEqual(35, self.validate({"type": "smart-note"}, {"bgAlpha": "35"})["bgAlpha"])
        self.assert_rejected({"type": "smart-note"}, {"bgAlpha": "半透明"}, "便签背景透明度无效")

    def test_note_only_fields_are_ignored_on_other_node_types(self):
        clean = self.validate({"type": "smart-prompt"}, {"bgAlpha": "半透明", "sizeMode": "huge"})
        self.assertEqual({"bgAlpha": "半透明", "sizeMode": "huge"}, clean)


class SmartNoteRenderContractTests(unittest.TestCase):
    def test_note_body_is_rendered_through_the_whitelist(self):
        body = extract_function("canvasOrganizerHtml")
        self.assertIn('contenteditable="true"', body)
        self.assertIn("${noteRichTextHtml(node)}", body)
        self.assertNotIn("<textarea class=\"smart-note-text\">", body)
        self.assertNotIn("escapeHtml(node.text||'')", body)

    def test_note_editing_keeps_its_dom_alive(self):
        # render() must not rebuild the editor mid-typing: a rebuild drops the
        # selection and the input-method composition buffer.
        self.assertIn("noteEditingIds.add(nodeForControls.id)", SMART_CANVAS_JS)
        self.assertIn("noteEditingIds.delete(nodeForControls.id);", SMART_CANVAS_JS)
        self.assertIn("promptTextEditingIds.has(node.id) || noteEditingIds.has(node.id)", SMART_CANVAS_JS)

    def test_autosize_never_clips_the_body(self):
        body = extract_function("fitSmartNoteToText")
        self.assertNotIn("Math.min(720", body)
        self.assertIn("noteContentOverflow(node, element)", body)

    def test_plain_drag_fixes_width_and_modifier_drag_scales_the_whole_note(self):
        self.assertIn("node.sizeMode = 'fixed';", SMART_CANVAS_JS)
        self.assertIn("resizeState.uniform = !!(e.altKey || e.ctrlKey || e.metaKey);", SMART_CANVAS_JS)
        self.assertIn(
            "const factor = Math.max(0.25, Math.min(4, Math.sqrt(widthRatio * heightRatio)));",
            SMART_CANVAS_JS,
        )
        self.assertIn("if(noteEl) fitNoteHeightToContent(node, noteEl);", SMART_CANVAS_JS)

    def test_dragging_inside_the_body_does_not_move_the_node(self):
        self.assertIn(".smart-node-floating-menu, .smart-note-text, .node-resize-handle", SMART_CANVAS_JS)


class SmartNoteFormattingContractTests(unittest.TestCase):
    """选中文字浮条 + 选中浮出菜单的静态契约（真实交互由人工在浏览器里验收）。"""

    def test_format_bar_keeps_the_selection_alive(self):
        body = extract_function("noteFormatBarEl")
        self.assertIn("bar.dataset.noteFormatBar = '1'", body)
        # 按下就阻止默认，否则点按钮会先折叠选区，命令作用不到选中的文字。
        self.assertIn("bar.addEventListener('mousedown', event => { event.preventDefault(); event.stopPropagation(); });", body)
        self.assertIn("runNoteFormatCommand(button.dataset.noteFormat)", body)

    def test_format_commands_produce_semantic_tags_for_the_sanitiser(self):
        body = extract_function("runNoteFormatCommand")
        self.assertIn("document.execCommand('styleWithCSS', false, false);", body)
        self.assertIn("pruneNoteEditorDom(editor);", body)
        self.assertIn("syncNoteFromEditor(node, editor);", body)
        self.assertIn("setNoteBlockAttribute(editor, 'data-fs', command.slice(3), '2')", body)

    def test_block_attributes_are_dropped_at_their_defaults(self):
        body = extract_function("setNoteBlockAttribute")
        self.assertIn("block.removeAttribute(name)", body)
        self.assertIn("block.setAttribute(name, value)", body)

    def test_checklist_and_link_use_the_whitelisted_shape(self):
        checklist = extract_function("toggleNoteChecklist")
        self.assertIn("document.execCommand('insertUnorderedList');", checklist)
        self.assertIn("item.setAttribute('data-checked', 'false');", checklist)
        link = extract_function("applyNoteLink")
        self.assertIn("noteRichTextSafeLink(String(answer || '').trim())", link)
        self.assertIn("document.execCommand('createLink', false, value);", link)
        self.assertIn("document.execCommand('unlink');", link)

    def test_size_buttons_cover_all_four_steps(self):
        self.assertIn(
            "const NOTE_FS_OPTIONS = [['1','smart.noteSizeSmall',11],['2','smart.noteSizeNormal',13],"
            "['3','smart.noteSizeLarge',15],['4','smart.noteSizeHuge',18]];",
            SMART_CANVAS_JS,
        )
        self.assertIn('data-note-format="fs-${value}"', extract_function("noteFormatBarHtml"))

    def test_selection_change_drives_the_bar(self):
        self.assertIn("document.addEventListener('selectionchange'", SMART_CANVAS_JS)
        body = extract_function("updateNoteFormatBar")
        self.assertIn("noteFormatBarNoteId !== state.node.id", body)
        self.assertIn("refreshIcons();", body)

    def test_note_card_swaps_the_permanent_toolbar_for_the_floating_menu(self):
        body = extract_function("canvasOrganizerHtml")
        self.assertIn("${smartNoteFloatingMenuHtml(node)}", body)
        self.assertNotIn("smart-note-toolbar", SMART_CANVAS_JS)
        self.assertNotIn("smart-note-toolbar", SMART_CANVAS_CSS)
        menu = extract_function("smartNoteFloatingMenuHtml")
        self.assertIn('class="smart-node-floating-menu smart-note-menu"', menu)
        self.assertIn('data-note-alpha="1"', menu)
        self.assertIn('data-note-size-mode="${nextMode}"', menu)
        self.assertIn('class="organizer-edit node-delete"', menu)

    def test_paste_prunes_the_clipboard_html(self):
        self.assertIn("data.getData('text/html')", SMART_CANVAS_JS)
        self.assertIn("sanitizeNoteRichText(html) || escapeHtml(data.getData('text/plain') || '')", SMART_CANVAS_JS)
        self.assertIn("document.execCommand('insertHTML', false, clean);", SMART_CANVAS_JS)

    def test_alpha_slider_only_touches_the_custom_property(self):
        self.assertIn(
            "nodeForControls.bgAlpha = Math.max(0, Math.min(100, Math.round(Number(event.target.value)) || 0));",
            SMART_CANVAS_JS,
        )
        self.assertIn("--note-bg-alpha", SMART_CANVAS_JS)

    def test_css_fades_every_layer_with_the_alpha(self):
        self.assertIn("var(--organizer-color) var(--note-bg-alpha, 20%)", SMART_CANVAS_CSS)
        self.assertIn(".smart-note-format-bar[hidden] { display:none; }", SMART_CANVAS_CSS)
        self.assertIn('.smart-note-text [data-fs="4"] { font-size:1.6em; }', SMART_CANVAS_CSS)
        self.assertIn('.smart-note-text li[data-checked="true"]', SMART_CANVAS_CSS)

    def test_bullet_and_numbered_lists_are_in_the_bar(self):
        body = extract_function("runNoteFormatCommand")
        self.assertIn("document.execCommand('insertUnorderedList');", body)
        self.assertIn("document.execCommand('insertOrderedList');", body)
        bar = extract_function("noteFormatBarHtml")
        self.assertIn("iconButton('ul', 'list', tr('smart.noteBulletList'))", bar)
        self.assertIn("iconButton('ol', 'list-ordered', tr('smart.noteNumberedList'))", bar)
        self.assertIn("active.list === 'ul'", extract_function("refreshNoteFormatBarState"))

    def test_paragraph_containers_stay_in_the_whitelist(self):
        # Chromium 按 Enter 产出 <div>、粘贴常见 <p>；把它们解包就等于保存时丢掉所有换行。
        self.assertIn("'p','div','br','a'", SMART_CANVAS_JS)
        self.assertIn("['h1','h2','h3','li','p','div']", SMART_CANVAS_JS)
        self.assertIn('"p", "div", "br", "a"', MAIN_PY)
        self.assertIn('("h1", "h2", "h3", "li", "p", "div")', MAIN_PY)

    def test_note_menu_overrides_the_generic_floating_button_sizing(self):
        # `.smart-node-floating-menu button`（1 class + 1 element）压过单类的
        # `.organizer-color` / `.smart-text-copy-btn`：不加双类覆盖，色块会被撑成
        # 34x24 的透明药丸、复制按钮会丢边框。回归防线，勿删。
        self.assertIn(".smart-note-menu .smart-note-menu-colors .organizer-color {", SMART_CANVAS_CSS)
        self.assertIn("background:var(--swatch);", SMART_CANVAS_CSS)
        self.assertIn(".smart-note-menu .smart-text-copy-btn {", SMART_CANVAS_CSS)

    def test_rich_text_is_never_written_to_inner_html(self):
        # 存储里的 HTML 只以文本形式存在：渲染必须过 noteRichTextHtml()，
        # 写回必须过 sanitizeNoteRichText()，否则一次保存就能把脏 HTML 固化下来。
        for pattern in ("innerHTML = node.richText", "innerHTML=node.richText",
                        "innerHTML = editor.innerHTML", "insertAdjacentHTML('beforeend', node.richText"):
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, SMART_CANVAS_JS)
        write = extract_function("syncNoteFromEditor")
        self.assertIn("sanitizeNoteRichText(editor.innerHTML)", write)
        render = extract_function("canvasOrganizerHtml")
        self.assertIn("${noteRichTextHtml(node)}", render)

    def test_every_note_formatting_key_is_translated(self):
        keys = set(re.findall(r"trf?\('(smart\.note[A-Za-z0-9]*)'", SMART_CANVAS_JS))
        options = re.search(r"const NOTE_FS_OPTIONS = \[(.*?)\];", SMART_CANVAS_JS).group(1)
        keys.update(re.findall(r"'(smart\.note[A-Za-z0-9]+)'", options))
        self.assertTrue(keys)
        for key in sorted(keys):
            with self.subTest(key=key):
                entry = re.search(rf'"{re.escape(key)}": \{{ zh: "([^"]*)", en: "([^"]*)" \}}', SMART_CANVAS_I18N)
                self.assertIsNotNone(entry, f"{key} is missing from the i18n map")
                self.assertTrue(entry.group(1).strip() and entry.group(2).strip(), key)


if __name__ == "__main__":
    unittest.main()
