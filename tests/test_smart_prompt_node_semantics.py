import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")


def extract_function(name: str) -> str:
    marker = f"function {name}("
    start = SMART_CANVAS_JS.index(marker)
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


class SmartPromptNodeSemanticsTests(unittest.TestCase):
    def test_preset_only_text_propagates_to_text_image_and_video_nodes(self):
        functions = "\n".join(
            extract_function(name)
            for name in (
                "promptNodeSeparator",
                "promptNodePromptItems",
                "promptTextItemsForNode",
                "promptNodeUpstreamPromptItems",
                "promptNodeUpstreamPromptText",
                "promptNodeLLMInputText",
                "textForNode",
                "inputPromptTextFor",
            )
        )
        script = f"""
const assert = require('assert');
const window = {{
    promptPresetComposeText(node, rawText) {{
        const presets = Array.isArray(node?.promptPresets) ? node.promptPresets : [];
        const prefix = presets.map(item => String(item?.prefix || '').trim()).filter(Boolean);
        const suffix = presets.map(item => String(item?.suffix || '').trim()).filter(Boolean);
        return [...prefix, String(rawText || '').trim(), ...suffix].filter(Boolean).join('\\n\\n');
    }}
}};
const smartLoopContext = {{}};
const nodes = [];
const orderedPromptNodeInputs = node => node?.inputs || [];
const promptInputNodesFor = node => node?.inputs || [];
const smartLoopPrompt = () => '';
const smartGroupMembers = () => [];
{functions}

const presetOnly = {{
    type:'smart-prompt',
    text:'',
    promptPresets:[{{prefix:'反推提示词', suffix:'只输出最终提示词'}}]
}};
assert.deepStrictEqual(
    promptNodePromptItems(presetOnly),
    ['反推提示词\\n\\n只输出最终提示词']
);

const downstreamText = {{
    type:'smart-prompt',
    inputs:[presetOnly],
    llmInstruction:'适配 GPT Image 2'
}};
assert.strictEqual(
    promptNodeLLMInputText(downstreamText),
    '反推提示词\\n\\n只输出最终提示词\\n\\n适配 GPT Image 2'
);

for (const type of ['smart-image-generation', 'smart-video-generation']) {{
    assert.strictEqual(
        inputPromptTextFor({{type, inputs:[presetOnly]}}),
        '反推提示词\\n\\n只输出最终提示词'
    );
}}

const bodyAndPreset = {{
    type:'smart-prompt',
    text:'主体内容',
    promptPresets:[{{prefix:'前缀', suffix:'后缀'}}]
}};
assert.deepStrictEqual(promptNodePromptItems(bodyAndPreset), ['前缀\\n\\n主体内容\\n\\n后缀']);
assert.deepStrictEqual(promptNodePromptItems({{type:'smart-prompt', text:'', promptPresets:[]}}), []);
"""
        run_node(script)

    def test_panel_dom_guard_only_preserves_text_editors(self):
        function = extract_function("isTextNodePanelEditingTarget")
        script = f"""
const assert = require('assert');
{function}
function element(tagName, contenteditable=false) {{
    return {{
        matches(selector) {{
            const selectors = selector.split(',').map(item => item.trim());
            return selectors.includes(tagName) || (contenteditable && selectors.includes('[contenteditable="true"]'));
        }}
    }};
}}
assert.strictEqual(isTextNodePanelEditingTarget(element('textarea')), true);
assert.strictEqual(isTextNodePanelEditingTarget(element('input')), true);
assert.strictEqual(isTextNodePanelEditingTarget(element('div', true)), true);
assert.strictEqual(isTextNodePanelEditingTarget(element('button')), false);
assert.strictEqual(isTextNodePanelEditingTarget(element('select')), false);
assert.strictEqual(isTextNodePanelEditingTarget(null), false);
"""
        run_node(script)

    def test_render_guard_uses_editing_target_check(self):
        render_function = extract_function("renderTextNodePanel")
        self.assertIn("activeInsidePanelEditor", render_function)
        self.assertIn("isTextNodePanelEditingTarget(document.activeElement)", render_function)
        self.assertNotIn("if(activeInsidePanel){", render_function)


if __name__ == "__main__":
    unittest.main()
