import asyncio
import os
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class SmartCanvasOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.workspace_js = (cls.root / "static" / "js" / "canvas-list.js").read_text(encoding="utf-8")
        cls.smart_canvas_js = (cls.root / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_classic_canvas_static_resources_are_removed(self):
        self.assertFalse((self.root / "static" / "canvas.html").exists())
        self.assertFalse((self.root / "static" / "js" / "canvas.js").exists())
        self.assertFalse((self.root / "static" / "css" / "canvas.css").exists())
        self.assertFalse((self.root / "static" / "js" / "i18n" / "canvas.js").exists())

    def test_canvas_creation_accepts_only_smart_kind(self):
        payload = main.CanvasCreateRequest(title="智能画布", kind="smart")
        with mock.patch.object(main, "new_canvas", return_value={"id": "smart-1", "kind": "smart"}), \
             mock.patch.object(main, "require_authenticated", return_value={"id": "creator-1"}):
            result = asyncio.run(main.create_canvas(payload, object()))
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.create_canvas(main.CanvasCreateRequest(title="不支持的画布", kind="unsupported"), object()))
        self.assertEqual(result["canvas"]["kind"], "smart")
        self.assertEqual(caught.exception.status_code, 400)

    def test_project_workspace_opens_only_smart_canvas(self):
        self.assertIn("/static/smart-canvas.html", self.workspace_js)
        self.assertNotIn("/static/canvas.html", self.workspace_js)
        self.assertNotIn("ws-legacy-section", self.workspace_js)

    def test_composer_run_uses_its_own_node_context_and_snapshot(self):
        source = self.smart_canvas_js
        run_start = source.index("async function runGeneration(targetNode=null){")
        run_end = source.index("const SMART_TEXT_GENERATION_TIMEOUT_MS", run_start)
        run_body = source[run_start:run_end]

        self.assertIn("const composerContext = {nodeId:'', revision:0};", source)
        self.assertIn("const node = targetNode || activeComposerNode();", run_body)
        self.assertNotIn("const node = selectedNode();", run_body)
        self.assertIn("const request = buildRunRequestForNode(node, null, true, smartLoopContext);", run_body)
        self.assertIn("smartRunSnapshot(node, prompt, refs, logKind, runSettings)", run_body)
        self.assertIn("clearPromptInputForNode(node, {preserveDraft:true});", run_body)
        self.assertIn("await runGeneration(node);", source)

    def test_node_field_deletion_travels_as_clear_fields(self):
        source = self.smart_canvas_js
        # A narrow node operation only carries present, changed values, so a field
        # the client really deleted (for example the last reference image) must be
        # declared explicitly — otherwise the server keeps the stale value and the
        # next node broadcast resurrects it in the composer.
        self.assertIn("const clearFields = nodeFieldDeletions(node, previous);", source)
        self.assertIn("operations.push({kind:'node_fields', node_id:id, fields, clear_fields:clearFields})", source)
        self.assertIn("function nodeFieldDeletions(node, previous){", source)
        deletion_start = source.index("const clearNodeFields = target => {")
        deletion_end = source.index("};", deletion_start)
        deletion_body = source[deletion_start:deletion_end]
        self.assertIn("!CANVAS_OPERATION_RESERVED_NODE_FIELDS.has(key)", deletion_body)
        self.assertIn("delete target[key];", deletion_body)
        self.assertNotIn("allowed.has(field)", deletion_body)

    def test_generation_settings_memory_is_canvas_scoped_and_context_isolated(self):
        source = self.smart_canvas_js
        self.assertIn("const SMART_GENERATION_SETTINGS_MEMORY_VERSION = 1;", source)
        self.assertIn("canvas.generationSettingsMemory = {version:SMART_GENERATION_SETTINGS_MEMORY_VERSION, contexts:{}};", source)
        self.assertIn("`${kind}::${engine}::${provider || '__default__'}::${hasReference ? 'reference' : 'text'}`", source)
        self.assertIn("settingsMemoryManaged:options.settingsMemory !== false", source)
        self.assertIn("restoreSmartGenerationSettingsMemory(subject);", source)
        self.assertIn("rememberSmartGenerationSettingsMemory(subject, settings);", source)
        self.assertIn("if(!isVideo) base.resolution = smartFreshImageResolution(base);", source)

    def test_recoverable_upstream_task_stops_the_live_timer(self):
        source = self.smart_canvas_js
        recover_start = source.index("if(e && e.imageTaskRecover && e.recoverTaskId){")
        recover_end = source.index("return;", recover_start)
        recover_body = source[recover_start:recover_end]
        self.assertIn("node.pending = 0;", recover_body)
        self.assertIn("node.running = false;", recover_body)

    def test_canvas_media_catalog_preserves_replaced_media_for_authorized_lookup(self):
        canvas = {
            "nodes": [],
            "media_catalog": [{"url": "/output/previous-image.png", "kind": "image"}],
        }
        self.assertTrue(main.canvas_contains_media(canvas, "/output/previous-image.png"))

    def test_canvas_save_contract_and_picker_include_media_catalog(self):
        payload = main.CanvasSaveRequest(media_catalog=[{"url": "/output/previous-image.png"}])
        self.assertEqual(payload.media_catalog[0]["url"], "/output/previous-image.png")
        self.assertIn("function mergeCanvasMediaCatalog", self.smart_canvas_js)
        # Node-operation sync persists the media catalog as narrow item ops, never
        # a whole-canvas snapshot overwrite.
        self.assertIn("kind:'media_catalog_add'", self.smart_canvas_js)
        self.assertIn("kind:'media_catalog_remove'", self.smart_canvas_js)
        self.assertIn("function removeCanvasMediaCatalogItem", self.smart_canvas_js)

    def test_workflow_create_dialog_events_wait_for_dom_ready(self):
        source = self.smart_canvas_js
        binding_start = source.index("window.addEventListener('DOMContentLoaded', () => {")
        binding_end = source.index("fileInput.onchange", binding_start)
        bindings = source[binding_start:binding_end]
        self.assertIn("workflowCreateClose", bindings)
        self.assertIn("workflowCreateCancel", bindings)
        self.assertIn("workflowCreateConfirm", bindings)
        self.assertIn("confirmWorkflowCreate().catch", bindings)

    def test_run_shortcut_runs_the_current_composer_node(self):
        source = self.smart_canvas_js
        # 快捷键必须复用 Composer“运行”按钮绑定的节点，而不是瞬时选区。
        resolve_start = source.index("function smartRunShortcutNode(){")
        resolve_end = source.index("function smartRunShortcutBlocked(", resolve_start)
        resolve_body = source[resolve_start:resolve_end]
        self.assertIn("String(runBtn?.dataset?.nodeId || '')", resolve_body)
        self.assertIn("nodes.find(node => node.id === boundNodeId)", resolve_body)
        self.assertIn("activeComposerNode()", resolve_body)
        self.assertIn("selectedNode()", resolve_body)
        # 文本节点的“生成”表单跟随选区展示，必须优先按选区解析，不能用运行按钮的绑定覆盖它。
        self.assertLess(
            resolve_body.index("if(selected?.type === 'smart-prompt') return selected;"),
            resolve_body.index("runBtn?.dataset?.nodeId"),
        )

        run_start = source.index("function runCurrentSmartNodeFromShortcut(){")
        run_end = source.index("runBtn.onclick = () => {", run_start)
        run_body = source[run_start:run_end]
        # 文本节点：等价于点击节点里的“生成”。
        self.assertIn("if(node?.type === 'smart-prompt'){", run_body)
        self.assertIn("runPromptLLMNode(node.id)", run_body)
        self.assertIn("if(!isSmartRunnableNode(node)){", run_body)
        self.assertIn("toast(tr('smart.shortcutRunNoNode'));", run_body)
        self.assertIn("if(runBtn?.disabled){", run_body)
        self.assertIn("runGeneration(node)", run_body)

        binding = source.index("(e.ctrlKey || e.metaKey) && e.shiftKey && key === 'enter'")
        binding_body = source[binding:binding + 400]
        self.assertIn("if(e.isComposing) return;", binding_body)
        self.assertIn("if(smartRunShortcutBlocked(e.target)) return;", binding_body)
        self.assertIn("e.preventDefault();", binding_body)
        self.assertIn("runCurrentSmartNodeFromShortcut();", binding_body)

        # Composer 与文本节点“生成”表单内可直接运行；图片编辑器和其他输入框内不触发；
        # “生成要求”文本框自己绑定 Ctrl+Enter，必须排除以免同一次按键重复提交。
        blocked_start = source.index("function smartRunShortcutBlocked(target){")
        blocked_end = source.index("function runCurrentSmartNodeFromShortcut(){", blocked_start)
        blocked_body = source[blocked_start:blocked_end]
        self.assertIn("imageEditModal?.classList?.contains('open')", blocked_body)
        self.assertIn("el.closest('#composer, .text-node-generation-panel')", blocked_body)
        self.assertIn("el.closest('.prompt-node-control, .text-node-input-card')", blocked_body)
        self.assertIn("return Boolean(el.closest('.text-node-requirement'));", blocked_body)
        self.assertIn("isEditableTarget(el)", blocked_body)

    def test_run_shortcut_is_documented_in_the_shortcut_panel(self):
        html = (self.root / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        i18n = (self.root / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn('data-i18n="smart.shortcutRun"', html)
        self.assertIn('<kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>Enter</kbd>', html)
        for key in ("smart.shortcutRun", "smart.shortcutRunNoNode"):
            lines = [line for line in i18n.splitlines() if f'"{key}":' in line]
            self.assertEqual(len(lines), 1, f"{key} must be defined exactly once")
            self.assertIn("{ zh:", lines[0])
            self.assertIn(", en:", lines[0])

    def test_comfy_workflow_sources_are_separated_with_legacy_system_names(self):
        system_name = "comfyui-workflow-multiple-angles-api.json"
        self.assertEqual(
            main.normalized_workflow_name(system_name),
            f"system/{system_name}",
        )
        system_items = main.list_workflows(source="system")["workflows"]
        custom_items = main.list_workflows(source="custom")["workflows"]
        self.assertTrue(system_items)
        self.assertTrue(all(item["source"] == "system" for item in system_items))
        self.assertTrue(all(item["source"] == "custom" for item in custom_items))
        with self.assertRaises(HTTPException) as caught:
            main.delete_workflow(f"system/{system_name}")
        self.assertEqual(caught.exception.status_code, 400)

    def test_comfy_execution_failover_only_accepts_explicit_node_errors(self):
        execution_error = {
            "status": {
                "messages": [["execution_error", {
                    "node_id": "2",
                    "node_type": "Mask_Remove_bg2",
                    "exception_message": "model file not found",
                }]],
            },
        }
        self.assertIn("model file not found", main.comfy_history_execution_failure(execution_error))
        self.assertEqual(main.comfy_history_execution_failure({"outputs": {}}), "")
        self.assertEqual(
            main.comfy_history_execution_failure({"status": {"messages": [["execution_cached", {"node_id": "2"}]]}}),
            "",
        )

    def test_comfy_execution_failover_walks_all_remaining_backends(self):
        self.assertEqual(
            main.comfy_execution_retry_backends(["first:8188", "second:8188", "third:8188"], "first:8188"),
            ["second:8188", "third:8188"],
        )
        self.assertEqual(main.comfy_execution_retry_backends(["first:8188"], "first:8188"), [])

    def test_comfy_mask_field_is_persisted_and_composed_with_its_image_field(self):
        field = main.WorkflowField(id="source_mask", type="mask", **{"for": "source"})
        self.assertEqual(field.for_field, "source")
        self.assertEqual(field.dict(by_alias=True)["for"], "source")
        self.assertIn("function assignComfyWorkflowMediaValues", self.smart_canvas_js)
        self.assertIn("function comfyNameForMaskedRef", self.smart_canvas_js)
        self.assertIn("function comfyImageFieldConsumesMask", self.smart_canvas_js)
        self.assertIn("node.class_type === 'LoadImage'", self.smart_canvas_js)
        self.assertIn("assignComfyWorkflowMediaValues(fields, values, allRefs, wf.workflow)", self.smart_canvas_js)
        self.assertIn("mask:{url:file.url, name:file.name, kind:'image'}", self.smart_canvas_js)
        self.assertIn("mask_url:ref.mask?.url || ''", self.smart_canvas_js)
        self.assertIn("String(ref.role || '').toLowerCase() !== 'mask'", self.smart_canvas_js)
        self.assertIn("['image','mask','video','audio']", self.smart_canvas_js)

    def test_comfy_upload_names_are_unique_and_upscale_requires_staged_input(self):
        first_folder, first_name = main.comfy_staging_upload_target("image.png")
        second_folder, second_name = main.comfy_staging_upload_target("image.png")
        self.assertTrue(first_folder.startswith("infinite-canvas/"))
        self.assertEqual(first_folder, second_folder)
        self.assertNotEqual(first_name, second_name)
        self.assertEqual(main.comfy_staging_relative_name(first_folder, first_name), f"{first_folder}/{first_name}")
        valid = main.GenerateRequest(
            workflow_json="system/2K高清放大-Klein 9B-api.json",
            params={"157": {"image": f"{first_folder}/{first_name}"}},
        )
        main.require_comfy_upscale_input(valid)
        with self.assertRaises(HTTPException) as caught:
            main.require_comfy_upscale_input(main.GenerateRequest(
                workflow_json="system/2K高清放大-Klein 9B-api.json",
                params={"157": {"image": "image.png"}},
            ))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertNotIn("if(ref.comfy_name) return ref.comfy_name;", self.smart_canvas_js)

    def test_comfy_input_sync_preserves_the_staging_subfolder(self):
        response = mock.Mock()
        response.json.return_value = {"name": "abc.png", "subfolder": "infinite-canvas/2026-09"}
        with mock.patch.object(main.requests, "post", return_value=response) as post:
            main.upload_comfy_input_bytes("worker:8188", "infinite-canvas/2026-09/abc.png", b"image", "image/png")
        self.assertEqual(post.call_args.kwargs["files"]["image"][0], "abc.png")
        self.assertEqual(post.call_args.kwargs["data"], {"type": "input", "subfolder": "infinite-canvas/2026-09", "overwrite": "true"})

    def test_comfy_input_view_uses_separate_filename_and_subfolder(self):
        url = main.comfy_input_view_url("worker:8188", "infinite-canvas/2026-09/abc.png")
        self.assertEqual(urlparse(url).path, "/view")
        self.assertEqual(parse_qs(urlparse(url).query), {
            "filename": ["abc.png"],
            "subfolder": ["infinite-canvas/2026-09"],
            "type": ["input"],
        })
        self.assertEqual(main.comfy_input_path_parts("plain.png"), ("plain.png", ""))

    def test_comfy_staging_cleanup_never_touches_manual_input(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir) / "infinite-canvas" / "2026-08"
            staging.mkdir(parents=True)
            old_file = staging / "old.png"
            old_file.write_bytes(b"old")
            os.utime(old_file, (now - 20 * 86400, now - 20 * 86400))
            manual = Path(temp_dir) / "manual-input.png"
            manual.write_bytes(b"keep")
            with mock.patch.object(main, "COMFYUI_INPUT_DIRS", [temp_dir]), \
                 mock.patch.object(main, "COMFY_STAGING_RETENTION_DAYS", 14), \
                 mock.patch.object(main, "COMFY_STAGING_MAX_BYTES", 0):
                result = main.clean_comfy_staging_inputs(now)
            self.assertEqual(result["files"], 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(manual.exists())

    def test_comfy_custom_workflow_without_prompt_field_does_not_require_prompt(self):
        # 未映射提示词字段的自定义工作流不应强制要求提示词，也不应显示“需要提示词”的占位提示。
        self.assertIn("function smartRunNeedsPrompt", self.smart_canvas_js)
        self.assertIn("comfyWorkflowCache[sourceSettings.comfyWorkflow]?.config?.fields", self.smart_canvas_js)
        self.assertIn("function ensureComfyWorkflowCachedForPromptCheck", self.smart_canvas_js)
        self.assertIn("function hideComfyNoPromptHint", self.smart_canvas_js)
        self.assertIn("smart.comfyNoPromptHint", self.smart_canvas_js)
        smart_canvas_html = (self.root / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        self.assertIn('id="comfyNoPromptHint"', smart_canvas_html)


if __name__ == "__main__":
    unittest.main()
