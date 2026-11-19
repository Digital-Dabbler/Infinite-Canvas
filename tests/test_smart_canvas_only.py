import asyncio
import os
import re
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

    def test_comfy_execution_failure_detail_reports_node_error_without_tensor_dump(self):
        tensor_text = "tensor([[[[0.2314, 0.1804, 0.2902], [0.2353, 0.1765, 0.3020]]]])"
        payload = {
            "node_id": "7",
            "node_type": "Mask_Remove_bg2",
            "exception_type": "RuntimeError",
            "exception_message": (
                "Error in image processing: Error in Inspyrenet processing: "
                "cannot import name 'median_blur' from 'albucore'"
            ),
            "current_inputs": {"image": [tensor_text * 200]},
            "current_outputs": {"image": [tensor_text * 200]},
            "traceback": ["Traceback (most recent call last):", tensor_text * 200],
        }
        detail = main.comfy_history_execution_failure({
            "status": {"messages": [["execution_error", payload]]},
        })
        self.assertIn("node 7", detail)
        self.assertIn("Mask_Remove_bg2", detail)
        self.assertIn("RuntimeError", detail)
        self.assertIn("median_blur", detail)
        self.assertNotIn("tensor(", detail)
        self.assertLessEqual(len(detail), main.COMFY_EXECUTION_ERROR_TEXT_LIMIT)

    def test_comfy_execution_failure_detail_handles_plain_payload(self):
        detail = main.comfy_history_execution_failure({
            "status": {"messages": [["execution_error", "plain backend failure"]]},
        })
        self.assertIn("plain backend failure", detail)

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


class SmartCanvasAutoLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.smart_canvas_js = (cls.root / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.smart_canvas_html = (cls.root / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.smart_canvas_css = (cls.root / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        cls.smart_canvas_i18n = (cls.root / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_layout_actions_mutate_through_undo_render_and_narrow_save(self):
        # 排版是用户可撤销的批量结构变化：每个动作必须一次 pushUndo，随后 render + scheduleSave，
        # 由既有 node_fields 窄操作同步 x/y/w/h，不引入整画布回写。
        source = self.smart_canvas_js
        start = source.index("// ---- 多选自动排版（对齐 / 等距 / 等尺寸 / 网格，浮动工具栏触发） ----")
        end = source.index("function applyViewport(){", start)
        body = source[start:end]
        for fn in (
            "function layoutSelectionUnits(){",
            "function translateLayoutUnit(unit, dx, dy){",
            "function alignSelectedSmartNodes(mode){",
            "function distributeSelectedSmartNodes(axis){",
            "function sizeSelectedSmartNodes(dim){",
            "function gridSelectedSmartNodes(){",
        ):
            self.assertIn(fn, body)
        self.assertEqual(body.count("pushUndo();"), 4)
        self.assertEqual(body.count("render();"), 4)
        self.assertEqual(body.count("scheduleSave();"), 4)
        self.assertNotIn("saveCanvasOperations(", body)
        # workflow 组织框是原子单元：整体平移成员后重算框，不参与等宽等高缩放。
        self.assertIn("fitWorkflowOrganizerBounds(unit);", body)
        self.assertIn("if(isWorkflowOrganizerNode(unit)) return;", body)

    def test_layout_toolbar_shows_only_for_multi_selection_and_is_wired(self):
        html = self.smart_canvas_html
        self.assertIn('id="layoutToolbar"', html)
        actions = [
            "align-left", "align-hcenter", "align-right",
            "align-top", "align-vcenter", "align-bottom",
            "distribute-h", "distribute-v",
            "equal-width", "equal-height", "grid",
        ]
        for action in actions:
            self.assertIn(f'data-layout-action="{action}"', html)
        source = self.smart_canvas_js
        self.assertIn("const layoutToolbar = document.getElementById('layoutToolbar');", source)
        self.assertIn("return units.length >= 2 ? units : [];", source)
        self.assertIn("button.disabled = units.length < 3;", source)
        # 生命周期钩子：选择变化、渲染、视口平移缩放、拖拽隐藏/恢复。
        self.assertIn("updateWorkflowGroupToolbar();\n    updateLayoutToolbar();", source)
        self.assertIn("positionWorkflowGroupToolbar();\n        positionLayoutToolbar();", source)
        self.assertIn("hideWorkflowGroupToolbarDuringDrag(e);\n    hideLayoutToolbarDuringDrag(e);", source)
        self.assertIn("restoreWorkflowGroupToolbarAfterDrag();\n        restoreLayoutToolbarAfterDrag();", source)
        self.assertIn("#layoutToolbar { position:absolute;", self.smart_canvas_css)
        self.assertIn("#layoutToolbar.open { display:flex; }", self.smart_canvas_css)
        # 用户确认的视觉规则：工具栏整体放大，横向对齐图标与纵向同款（对象+基准线风格）。
        self.assertIn(".layout-toolbar-floating button { width:44px;", self.smart_canvas_css)
        # 图标按用户草图手绘：基准线 + 两个尺寸不同、贴齐基准线的色块。
        # 用户明确保留等距/等宽/等高四个按钮原有的图标，不要改动它们。
        buttons = re.findall(
            r'<button[^>]*data-layout-action="([^"]+)"[^>]*>(.*?)</button>', html, re.S)
        self.assertEqual(len(buttons), 11)
        icon_map = dict(buttons)
        for action in (
            "align-left", "align-hcenter", "align-right",
            "align-top", "align-vcenter", "align-bottom",
        ):
            inner = icon_map[action]
            self.assertIn('<svg class="layout-icon"', inner, action)
            self.assertIn('<path d=', inner, action)
            self.assertEqual(inner.count("<rect"), 2, action)
        for action in ("distribute-h", "distribute-v", "equal-width", "equal-height"):
            self.assertIn("<i data-lucide=", icon_map[action], action)
        self.assertIn("<rect", icon_map["grid"])
        self.assertIn(".layout-toolbar-floating .layout-icon { fill:none; stroke:currentColor;",
                      self.smart_canvas_css)

    def test_layout_vertical_icons_snap_blocks_to_baseline(self):
        # 用户拍板（方案B）：顶部/垂直居中/底部三个图标，色块紧贴基准线——
        # 顶对齐块挂线下、底对齐块坐线上、居中线穿过大块中部；
        # 且三者墨迹带都在 y2..y18 同一高度，整排视觉齐平不"高高低低"。
        html = self.smart_canvas_html
        buttons = re.findall(
            r'<button[^>]*data-layout-action="([^"]+)"[^>]*>(.*?)</button>', html, re.S)
        icon_map = dict(buttons)

        def rects(inner):
            return [tuple(int(v) for v in m) for m in re.findall(
                r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', inner)]

        top = icon_map["align-top"]
        self.assertIn('<path d="M3.5 2h17"', top)
        self.assertEqual(rects(top), [(5, 4, 6, 14), (15, 4, 5, 8)])

        vcenter = icon_map["align-vcenter"]
        self.assertIn('<path d="M2.5 10h19"', vcenter)
        big, small = rects(vcenter)
        self.assertEqual(big[1] + big[3] // 2, 10)  # 基准线穿过大块中部

        bottom = icon_map["align-bottom"]
        self.assertIn('<path d="M3.5 18h17"', bottom)
        self.assertEqual(rects(bottom), [(5, 2, 6, 16), (15, 10, 5, 8)])

        # 整排齐平：三个图标的墨迹带都在 y2..y18，同一高度不"高高低低"。
        for name, inner in (("align-top", top), ("align-vcenter", vcenter),
                            ("align-bottom", bottom)):
            ys_min = min(r[1] for r in rects(inner))
            ys_max = max(r[1] + r[3] for r in rects(inner))
            line_y = float(re.search(r'M[\d.]+ ([\d.]+)h', inner).group(1))
            self.assertEqual(min(ys_min, line_y), 2, name)
            self.assertEqual(max(ys_max, line_y), 18, name)

    def test_layout_distribute_uses_fixed_gap_not_first_last_anchor(self):
        # 用户确认的规则：等距不再以首尾节点为锚均分间隙，而是从首个单元起按固定间距排布。
        source = self.smart_canvas_js
        start = source.index("function distributeSelectedSmartNodes(axis){")
        end = source.index("function referenceLayoutRect(){", start)
        body = source[start:end]
        self.assertIn("const LAYOUT_FIXED_GAP = 40;", source[start - 200:end])
        self.assertIn("cursor += item.rect[extent] + LAYOUT_FIXED_GAP;", body)
        self.assertNotIn("(end - start - content)", body)
        grid_start = source.index("function gridSelectedSmartNodes(){")
        grid_end = source.index("function applyViewport(){", grid_start)
        self.assertIn("const gap = LAYOUT_FIXED_GAP;", source[grid_start:grid_end])

    def test_layout_equal_size_uses_first_selected_node_as_reference(self):
        # 用户确认的规则：等宽/等高/网格统一尺寸以第一个选中的节点为准，不是选区极值。
        source = self.smart_canvas_js
        start = source.index("function referenceLayoutRect(){")
        end = source.index("function sizeSelectedSmartNodes(dim){", start)
        body = source[start:end]
        self.assertIn("const refId = selectedNodeIds()[0];", body)
        grid_start = source.index("function gridSelectedSmartNodes(){")
        grid_end = source.index("function applyViewport(){", grid_start)
        self.assertIn("resizeLayoutUnits(units, refRect, 'both');", source[grid_start:grid_end])

    def test_layout_i18n_keys_exist_in_both_locales(self):
        for key in (
            "smart.layoutActions", "smart.layoutAlignLeft", "smart.layoutAlignHCenter",
            "smart.layoutAlignRight", "smart.layoutAlignTop", "smart.layoutAlignVCenter",
            "smart.layoutAlignBottom", "smart.layoutDistributeH", "smart.layoutDistributeV",
            "smart.layoutEqualWidth", "smart.layoutEqualHeight", "smart.layoutGrid",
            "smart.layoutToastAligned", "smart.layoutToastDistributed",
            "smart.layoutToastEqualSize", "smart.layoutToastGrid",
        ):
            self.assertIn(f'"{key}"', self.smart_canvas_i18n)
        for key in ("smart.layoutAlignLeft", "smart.layoutDistributeH", "smart.layoutGrid"):
            self.assertIn(f'data-i18n-title="{key}"', self.smart_canvas_html)
            self.assertIn(f'data-i18n="smart.layoutActions"', self.smart_canvas_html)


class SmartCanvasSplitMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.smart_canvas_js = (cls.root / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.smart_canvas_html = (cls.root / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.smart_canvas_css = (cls.root / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        cls.smart_canvas_i18n = (cls.root / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")

    def split_merge_section(self):
        source = self.smart_canvas_js
        start = source.index("// ---- 多图结果拆分 / 合并")
        end = source.index("// ---- 拆分结果浮层", start)
        return source[start:end]

    def test_split_merge_core_uses_undo_render_and_narrow_save(self):
        # 与排版同款生命周期：一次 pushUndo、render、scheduleSave，由既有节点操作 diff 同步，
        # 绝不直接整画布回写。
        body = self.split_merge_section()
        for fn in (
            "function smartNodeIsBusy(node){",
            "function splitableGenerationNode(node){",
            "function splitGenerationResults(nodeId, requestedIndexes){",
            "function mergeableMediaNodes(){",
            "function mergeSelectionKindInfo(){",
            "function mergeSelectedMediaNodes(){",
        ):
            self.assertIn(fn, body)
        self.assertEqual(body.count("pushUndo();"), 2)
        self.assertEqual(body.count("render();"), 2)
        self.assertEqual(body.count("scheduleSave();"), 2)
        self.assertNotIn("saveCanvasOperations(", body)
        # 拆分落点：原节点右侧、复用排版固定间距；busy/循环/历史节点拒绝拆分。
        self.assertIn("const gap = LAYOUT_FIXED_GAP;", body)
        self.assertIn("const startX = sourceRect.x + sourceRect.width + gap;", body)
        self.assertIn("if(!isSmartGenerationNode(node) || isHistoryGroupNode(node) || smartNodeIsBusy(node)) return null;", body)
        self.assertIn("if(node.loopRootId || Number.isFinite(Number(node.loopRoundIndex))) return null;", body)
        # 拆出节点携带来源引用与序号，供合并插回原位。
        self.assertIn("splitFromNodeId:source.id,", body)
        self.assertIn("splitIndex:index,", body)
        # 合并：混合图片+视频拒绝；同源单宿主插回；无宿主时首个节点转生成节点并迁移连线去重。
        self.assertIn("if(kinds.length > 1){ toast(tr('smart.mergeMixedKinds')); return false; }", body)
        self.assertIn("host.images.splice(Math.max(0, Math.min(host.images.length, entry.index)), 0, entry.image);", body)
        self.assertIn("host.type = kind === 'video' ? 'smart-video-generation' : 'smart-image-generation';", body)
        self.assertIn("const key = canvasConnectionKey({from, to, kind:conn.kind});", body)

    def test_split_badge_is_clickable_entry_in_both_render_paths(self):
        source = self.smart_canvas_js
        # 徽章在 nodeBodyHtml 与 selectGenerationResult 两处重建，都带拆分入口属性。
        self.assertEqual(source.count('data-split-badge="${escapeAttr(node.id)}"'), 1)
        self.assertEqual(source.count('data-split-badge="${escapeAttr(nodeId)}"'), 1)
        self.assertIn("role=\"button\" tabindex=\"0\" title=\"${escapeAttr(tr('smart.splitBadgeTitle'))}\"", source)
        # 绑定在 bindGenerationResultControls 内，dataset 防重绑，支持键盘。
        start = source.index("function bindGenerationResultControls(nodeEl, nodeId){")
        end = source.index("function selectGenerationResult(nodeId, requestedIndex){", start)
        body = source[start:end]
        self.assertIn(".generation-result-count[data-split-badge]", body)
        self.assertIn("badge.dataset.splitBound === '1'", body)
        self.assertIn("openSplitResultsPanel(nodeId, badge);", body)
        # CSS 只对带 data-split-badge 的徽章放开点击，pending 覆盖层时收回。
        self.assertIn(".generation-result-count[data-split-badge] { pointer-events:auto; cursor:pointer; }", self.smart_canvas_css)
        self.assertIn(".generation-result-stack:has(.generation-pending-cell.overlay) .generation-result-count[data-split-badge] { pointer-events:none; }", self.smart_canvas_css)

    def test_split_results_panel_lifecycle_and_placement(self):
        source = self.smart_canvas_js
        start = source.index("// ---- 拆分结果浮层")
        end = source.index("function screenToWorld(event){", start)
        body = source[start:end]
        self.assertIn("function closeSplitResultsPanel(){", body)
        self.assertIn("function openSplitResultsPanel(nodeId, anchorEl){", body)
        # 外部点击（capture）与 Escape 关闭；视口变化时 applyViewport 首行关闭，避免面板脱离锚点。
        self.assertIn("document.addEventListener('pointerdown', onSplitResultsPanelPointerDown, true);", body)
        self.assertIn("document.addEventListener('keydown', onSplitResultsPanelKeyDown, true);", body)
        self.assertIn("if(event.key !== 'Escape') return;", body)
        self.assertEqual(body.count("splitGenerationResults(nodeId, [...selected]);"), 1)
        self.assertIn("function applyViewport(){\n    closeSplitResultsPanel();", source)
        # 勾选面板动作齐全：全选/清空/仅当前/确认。
        for action in ("all", "none", "current", "confirm"):
            self.assertIn(f"action === '{action}'", body)
        self.assertIn(".split-results-panel { position:fixed; z-index:120;", self.smart_canvas_css)
        # 滚轮在面板内翻阅列表而不是缩放画布：shell 缩放排除列表含面板，面板自身 stopPropagation 并把列表外滚动转发给网格。
        self.assertIn(".split-results-panel", source[source.index("shell.addEventListener('wheel'"):source.index("shell.ondragover")])
        self.assertIn("panel.addEventListener('wheel', event => {\n        event.stopPropagation();", body)
        self.assertIn("grid.scrollTop += event.deltaY;", body)

    def test_merge_pill_is_independent_container_wired_to_layout_toolbar(self):
        html = self.smart_canvas_html
        self.assertIn('id="mergeToolbar" class="workflow-group-toolbar workflow-group-toolbar-floating merge-toolbar-floating"', html)
        self.assertIn('data-merge-action="merge"', html)
        merge_buttons = re.findall(r'<button[^>]*data-merge-action="([^"]+)"', html)
        self.assertEqual(merge_buttons, ["merge"])
        # 独立于 #layoutToolbar：位于其后、photoshopContextMenu 之前。
        self.assertLess(html.index('id="layoutToolbar"'), html.index('id="mergeToolbar"'))
        self.assertLess(html.index('id="mergeToolbar"'), html.index('id="photoshopContextMenu"'))
        source = self.smart_canvas_js
        self.assertIn("const mergeToolbar = document.getElementById('mergeToolbar');", source)
        self.assertIn("updateMergeToolbar();\n    if(open) requestAnimationFrame(() => positionLayoutToolbar(units));", source)
        self.assertIn("positionMergeToolbar();\n}", source)
        self.assertIn("mergeToolbar?.classList.remove('open');", source)
        # 混选置灰 + 悬停提示；≥2 个单图节点才显示。
        self.assertIn("const open = items.length >= 2 && !layoutToolbarHiddenDuringDrag && !dragState && !resizeState;", source)
        self.assertIn("button.disabled = mixed;", source)
        self.assertIn("const {items, kinds} = mergeSelectionKindInfo();", source)
        # CSS：药丸无 translateX 居中（JS 直接给左边缘），与排版工具栏同 z 层。
        self.assertIn("#mergeToolbar { position:absolute; left:0; top:0; z-index:57; display:none; }", self.smart_canvas_css)
        self.assertIn("#mergeToolbar.open { display:flex; }", self.smart_canvas_css)
        self.assertIn(".merge-toolbar-floating { min-height:52px;", self.smart_canvas_css)

    def test_split_from_reference_is_remapped_on_restore_and_paste(self):
        source = self.smart_canvas_js
        self.assertIn("['workflowGroupId','sourceNodeId','historyFor','splitFromNodeId'].forEach(field => {", source)
        self.assertIn("['sourceNodeId','workflowGroupId','historyFor','splitFromNodeId'].forEach(key => {", source)

    def test_split_merge_i18n_keys_exist_in_both_locales(self):
        for key in (
            "smart.splitBadgeTitle", "smart.splitPanelTitle", "smart.splitSelectAll",
            "smart.splitSelectNone", "smart.splitSelectCurrent", "smart.splitConfirm",
            "smart.splitToastDone", "smart.splitBusyToast", "smart.splitNothingSelected",
            "smart.mergeAction", "smart.mergeMixedKinds", "smart.mergeToastDone",
        ):
            entry = re.search(rf'"{re.escape(key)}": \{{ zh: "(.+?)", en: "(.+?)" \}}', self.smart_canvas_i18n)
            self.assertIsNotNone(entry, key)
            self.assertTrue(entry.group(1).strip(), key)
            self.assertTrue(entry.group(2).strip(), key)
        # 确认按钮走 trf 的 {n} 占位。
        self.assertIn('"smart.splitConfirm": { zh: "拆分 {n} 张"', self.smart_canvas_i18n)
        self.assertIn('data-i18n-title="smart.mergeAction"', self.smart_canvas_html)


if __name__ == "__main__":
    unittest.main()
