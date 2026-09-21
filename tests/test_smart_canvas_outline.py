import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")


def extract_function(name: str) -> str:
    marker = f"function {name}("
    start = SMART_CANVAS_JS.index(marker)
    if SMART_CANVAS_JS[:start].endswith("async "):
        start -= len("async ")
    brace = SMART_CANVAS_JS.index("{", start)
    depth = 0
    for index in range(brace, len(SMART_CANVAS_JS)):
        char = SMART_CANVAS_JS[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return SMART_CANVAS_JS[start:index + 1]
    raise AssertionError(f"Unterminated JavaScript function: {name}")


def run_node(script: str) -> None:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)


HARNESS_HEADER = """
const assert = require('assert');
let nodes = [];
let searchValue = '';
const smartOutlineList = {innerHTML: ''};
const smartOutlineSearch = {get value(){ return searchValue; }};
const escapeHtml = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const escapeAttr = value => escapeHtml(value).replace(/"/g, '&quot;');
const organizerColor = node => node.color || '#f59e0b';
const workflowOrganizerMembers = node => node.members || [];
"""

HARNESS_BODY = """
const group = (id, title, description = '') => ({id, type: 'smart-workflow-group', title, description, members: [1, 2, 3]});
const note = (id, text) => ({id, type: 'smart-note', text});
const itemCount = () => (smartOutlineList.innerHTML.match(/smart-outline-item/g) || []).length;
const EMPTY = '<div class="smart-outline-empty">还没有工作流分组</div>';

// 1. 目录只列工作流分组，便签无论如何都不出现。
nodes = [group('g1', '角色设定'), note('n1', '第一行便签文本\\n第二行'), group('g2', '分镜')];
searchValue = '';
renderSmartOutline();
assert.strictEqual(itemCount(), 2);
assert.ok(smartOutlineList.innerHTML.includes('角色设定'));
assert.ok(smartOutlineList.innerHTML.includes('分镜'));
assert.ok(!smartOutlineList.innerHTML.includes('便签'));
assert.ok(!smartOutlineList.innerHTML.includes('第一行便签文本'));
assert.ok(smartOutlineList.innerHTML.includes('工作流分组'));
assert.ok(smartOutlineList.innerHTML.includes('3 节点'));

// 2. 搜索只匹配分组标题与说明：命中便签正文的词不会把便签捞回来。
searchValue = '便签';
renderSmartOutline();
assert.strictEqual(smartOutlineList.innerHTML, EMPTY);
searchValue = '分镜';
renderSmartOutline();
assert.strictEqual(itemCount(), 1);
assert.ok(smartOutlineList.innerHTML.includes('分镜'));
assert.ok(!smartOutlineList.innerHTML.includes('角色设定'));
searchValue = '一段说明文字';
nodes = [group('g3', '无匹配标题', '这里是一段说明文字')];
renderSmartOutline();
assert.strictEqual(itemCount(), 1);
assert.ok(smartOutlineList.innerHTML.includes('无匹配标题'));

// 3. 画布上只有便签时，目录是空态。
nodes = [note('n1', '只有便签')];
searchValue = '';
renderSmartOutline();
assert.strictEqual(smartOutlineList.innerHTML, EMPTY);

// 4. 分组标题为空时沿用占位标题，不产出 undefined。
nodes = [{id: 'g4', type: 'smart-workflow-group'}];
renderSmartOutline();
assert.ok(smartOutlineList.innerHTML.includes('未命名工作流'));
assert.ok(!smartOutlineList.innerHTML.includes('undefined'));
"""


class SmartCanvasOutlineTests(unittest.TestCase):
    def test_outline_lists_workflow_groups_only(self):
        functions = "\n".join(
            extract_function(name)
            for name in (
                "isWorkflowOrganizerNode",
                "renderSmartOutline",
            )
        )
        run_node(HARNESS_HEADER + functions + HARNESS_BODY)

    def test_outline_source_no_longer_handles_notes(self):
        body = extract_function("renderSmartOutline")
        # 便签曾被并进目录（isCanvasOrganizerNode = 分组 || 便签）；它不该回来。
        self.assertNotIn("isCanvasOrganizerNode", body)
        self.assertNotIn("isSmartNoteNode", body)
        self.assertNotIn("smart-note", body)
        self.assertIn("nodes.filter(isWorkflowOrganizerNode)", body)
        self.assertIn("还没有工作流分组", body)
        self.assertNotIn("还没有工作流分组或便签", body)

    def test_note_edits_do_not_rebuild_the_outline(self):
        marker = "const noteInput = el.querySelector('.smart-note-text');"
        start = SMART_CANVAS_JS.index(marker)
        handler = SMART_CANVAS_JS[start:SMART_CANVAS_JS.index("};", start) + 2]
        # 正文写入收敛到 syncNoteFromEditor（唯一写入口），编辑过程中不得重建目录。
        self.assertIn("syncNoteFromEditor(nodeForControls, noteInput, el);", handler)
        self.assertNotIn("renderSmartOutline", handler)


if __name__ == "__main__":
    unittest.main()
