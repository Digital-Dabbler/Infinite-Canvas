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


if __name__ == "__main__":
    unittest.main()
