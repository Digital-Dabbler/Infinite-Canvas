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
let workflowGroupRunState = null;
let selectedId = '';
let selectedIds = [];
let calls = [];
let members = [];
let llmResults = {};
let generationErrors = {};
const group = {id:'g1', type:'smart-workflow-group'};
const nodes = [group];
const canvas = {connections:[]};
const isWorkflowOrganizerNode = node => node?.type === 'smart-workflow-group';
const isSmartRunnableNode = node => node?.type === 'smart-image-generation';
const workflowGroupOrderedMembers = () => members;
const toast = message => calls.push(['toast', String(message)]);
const render = () => {};
const scheduleSave = () => {};
const tr = key => key;
const nodeById = id => nodes.find(node => node.id === id);
// stub 必须复刻真实行为：LLM 成功会把结果写回 node.text，失败只回报错误状态。
const runPromptLLMNode = async id => {
    calls.push(['llm', id]);
    const result = llmResults[id] || {status:'ok'};
    if(result.status === 'ok') nodeById(id).text = 'LLM 输出';
    return result;
};
const runGeneration = async node => {
    calls.push(['gen', node.id]);
    if(generationErrors[node.id]) node.error = generationErrors[node.id];
};
"""

HARNESS_BODY = """
const textNode = (id, instruction, text='手写提示词') => ({id, type:'smart-prompt', text, llmInstruction:instruction});
const imageNode = id => ({id, type:'smart-image-generation'});
const noteNode = id => ({id, type:'smart-note'});

async function scenario(list, results={}, errors={}) {
    calls = [];
    members = list;
    llmResults = results;
    generationErrors = errors;
    nodes.length = 0;
    nodes.push(group, ...list);
    canvas.connections = [];
    selectedId = 'before';
    selectedIds = ['before'];
    await runWorkflowGroup('g1');
}

(async () => {
    // 判定标准：只有“生成要求”有内容才把文本节点当作生成节点。
    assert.strictEqual(workflowGroupMemberRunMode(null), 'skip');
    assert.strictEqual(workflowGroupMemberRunMode({type:'smart-prompt', llmInstruction:''}), 'skip');
    assert.strictEqual(workflowGroupMemberRunMode({type:'smart-prompt', llmInstruction:'   \\n '}), 'skip');
    assert.strictEqual(workflowGroupMemberRunMode({type:'smart-prompt', llmInstruction:'反推提示词'}), 'text');
    assert.strictEqual(workflowGroupMemberRunMode({type:'smart-image-generation'}), 'generation');
    assert.strictEqual(workflowGroupMemberRunMode({type:'smart-note'}), 'skip');

    // 1. 生成要求有内容：先跑完文本节点的 LLM，下游图片节点随后读取新文本提交。
    const authored = textNode('t1', '反推这张图的提示词');
    await scenario([authored, imageNode('i1')]);
    assert.deepStrictEqual(calls, [['llm','t1'],['gen','i1'],['toast','整组执行完成']]);
    assert.strictEqual(authored.text, 'LLM 输出');
    assert.strictEqual(workflowGroupRunState, null);
    assert.strictEqual(selectedId, 'before');

    // 2. 生成要求为空：不调用 LLM，也不覆盖节点里手写的文本。
    const passthrough = textNode('t2', '   ');
    await scenario([passthrough, imageNode('i1')]);
    assert.deepStrictEqual(calls, [['gen','i1'],['toast','整组执行完成']]);
    assert.strictEqual(passthrough.text, '手写提示词');

    // 3. 便签等不可运行成员一律跳过。
    await scenario([noteNode('n1'), imageNode('i1')]);
    assert.deepStrictEqual(calls, [['gen','i1'],['toast','整组执行完成']]);

    // 4. LLM 失败（无可用模型、请求报错、超时）：立即中止整组。
    await scenario(
        [textNode('t1','反推这张图的提示词'), imageNode('i1')],
        {t1:{status:'error', message:'当前账号没有已配置的 LLM'}}
    );
    assert.deepStrictEqual(calls, [['llm','t1'],['toast','当前账号没有已配置的 LLM']]);
    assert.strictEqual(workflowGroupRunState, null);
    assert.strictEqual(selectedId, 'before');

    // 5. busy/cancelled 不中止整组：该节点已由别的执行接管。
    await scenario([textNode('t1','反推这张图的提示词'), imageNode('i1')], {t1:{status:'busy'}});
    assert.deepStrictEqual(calls, [['llm','t1'],['gen','i1'],['toast','整组执行完成']]);
    await scenario([textNode('t1','反推这张图的提示词'), imageNode('i1')], {t1:{status:'cancelled'}});
    assert.deepStrictEqual(calls, [['llm','t1'],['gen','i1'],['toast','整组执行完成']]);

    // 6. 生成节点自身的 node.error 依旧中止整组。
    await scenario([imageNode('i1')], {}, {i1:'上游提交失败'});
    assert.deepStrictEqual(calls, [['gen','i1'],['toast','上游提交失败']]);

    // 7. 循环链路跨出分组：直接拒绝，不执行任何成员。
    members = [textNode('t1','反推这张图的提示词'), imageNode('i1')];
    nodes.length = 0;
    nodes.push(group, ...members, {id:'l1', type:'smart-loop'});
    canvas.connections = [{from:'l1', to:'i1'}];
    calls = [];
    selectedId = 'before';
    await runWorkflowGroup('g1');
    assert.deepStrictEqual(calls, [['toast','循环链路不能跨出工作流分组']]);
    assert.strictEqual(workflowGroupRunState, null);
    assert.strictEqual(selectedId, 'before');
})().catch(error => { console.error(error); process.exit(1); });
"""


class WorkflowGroupTextRunTests(unittest.TestCase):
    def test_group_run_decides_per_member_and_stops_on_text_failure(self):
        functions = "\n".join(
            extract_function(name)
            for name in (
                "promptNodeHasTextGenerationRequest",
                "workflowGroupMemberRunMode",
                "runWorkflowGroup",
            )
        )
        run_node(HARNESS_HEADER + functions + HARNESS_BODY)

    def test_text_generation_reports_a_status_to_automation_callers(self):
        body = extract_function("runPromptLLMNode")
        # 返回值是整组执行的收口信号；早期返回也必须带状态，否则调用方会把
        # “未生成”误判成成功并继续跑下游节点。
        self.assertIn("if(!node || node.type !== 'smart-prompt') return {status:'skipped'};", body)
        self.assertIn("if(node.running) return {status:'busy'};", body)
        self.assertIn("return {status:'error', message:tr('smart.promptLlmNeedText')};", body)
        self.assertIn("return {status:'error', message:tr('smart.promptLlmNoneHint')};", body)
        self.assertIn("return {status:'ok'};", body)
        self.assertEqual(body.count("return {status:'cancelled'};"), 2)

    def test_group_run_waits_for_text_generation_before_downstream_nodes(self):
        body = extract_function("runWorkflowGroup")
        self.assertIn("const mode = workflowGroupMemberRunMode(node);", body)
        self.assertIn("const result = await runPromptLLMNode(node.id);", body)
        # 文本节点必须等待 LLM 结果落回 node.text，下游节点提交时才会用到新文本。
        self.assertLess(
            body.index("await runPromptLLMNode(node.id)"),
            body.index("await runGeneration(node);"),
        )
        self.assertIn("if(result?.status === 'error') throw new Error(result.message || tr('smart.promptLlmFailed'));", body)
        self.assertIn("if(node.error) throw new Error(node.error);", body)
        # 旧实现只用 isSmartRunnableNode 过滤成员，文本节点永远进不了整组执行。
        self.assertNotIn("if(!isSmartRunnableNode(node))continue;", body)


if __name__ == "__main__":
    unittest.main()
