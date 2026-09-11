import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

ROOT = Path(__file__).resolve().parents[1]
SMART_CANVAS_JS = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")


def extract_function(name):
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


def extract_const(name):
    marker = f"const {name} = "
    start = SMART_CANVAS_JS.index(marker)
    end = SMART_CANVAS_JS.index("\n", start)
    return SMART_CANVAS_JS[start:end]


def node_fields_payload(node_id, fields=None, clear_fields=None, operation_id="op-1"):
    return main.CanvasOperationRequest(
        operation_id=operation_id,
        client_id="client-1",
        kind="node_fields",
        node_id=node_id,
        fields=dict(fields or {}),
        clear_fields=list(clear_fields or []),
    )


class CanvasOperationFieldDeletionTests(unittest.TestCase):
    """A narrow node operation merges values, so a field the browser deleted
    used to survive on the server and was then written back into the canvas by
    any later node broadcast (for example a bound task completion).  The
    reported symptom was a reference image the user removed reappearing in the
    composer's 输入图 row right after a generation.  ``clear_fields`` makes the
    deletion travel as its own, validated part of the operation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.canvases = self.data / "canvases"
        self.canvases.mkdir(parents=True, exist_ok=True)
        self.canvas_id = "c-field-clear"
        self.patches = [
            patch.object(main, "DATA_DIR", str(self.data)),
            patch.object(main, "CANVAS_DIR", str(self.canvases)),
        ]
        for item in self.patches:
            item.start()
        self.canvas = {
            "id": self.canvas_id,
            "kind": "smart",
            "title": "test",
            "nodes": [self.node()],
            "connections": [],
            "logs": [],
            "viewport": {"x": 0, "y": 0, "scale": 1},
            "deleted_node_ids": [],
            "operation_log": [],
            "sync_revision": 0,
            "updated_at": 0,
        }
        main.save_canvas(self.canvas)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def node(self):
        return {
            "id": "generate_ref",
            "type": "smart-image-generation",
            "title": "图片生成",
            "images": [{"url": "/assets/output/a.png", "kind": "image"}],
            "activeImageIndex": 0,
            "manualInputRefs": [
                {"url": "/assets/input/removed_ref.png", "name": "removed.png", "kind": "image"},
            ],
            "runInputRefs": [{"url": "/assets/input/removed_ref.png"}],
            "runPromptRefs": [{"url": "/assets/input/removed_ref.png"}],
            "lastRunError": "旧失败信息",
        }

    def reload_node(self):
        return main.load_canvas(self.canvas_id)["nodes"][0]

    def test_removed_reference_is_deleted_server_side(self):
        canvas, changed = main.apply_canvas_node_operation(
            main.load_canvas(self.canvas_id),
            node_fields_payload("generate_ref", {"images": [{"url": "/assets/output/b.png"}]}, ["manualInputRefs"]),
        )
        self.assertTrue(changed)
        node = canvas["nodes"][0]
        self.assertNotIn("manualInputRefs", node)
        self.assertEqual(node["images"], [{"url": "/assets/output/b.png"}])
        # The deletion must survive a reload, not only the in-memory copy.
        self.assertNotIn("manualInputRefs", self.reload_node())

    def test_clear_fields_alone_is_a_valid_update(self):
        canvas, changed = main.apply_canvas_node_operation(
            main.load_canvas(self.canvas_id),
            node_fields_payload("generate_ref", {}, ["lastRunError", "runInputRefs", "runPromptRefs"]),
        )
        self.assertTrue(changed)
        node = canvas["nodes"][0]
        self.assertNotIn("lastRunError", node)
        self.assertNotIn("runInputRefs", node)
        self.assertNotIn("runPromptRefs", node)
        self.assertIn("manualInputRefs", node)

    def test_empty_fields_and_clear_fields_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            main.apply_canvas_node_operation(
                main.load_canvas(self.canvas_id),
                node_fields_payload("generate_ref", {}, []),
            )
        self.assertEqual(caught.exception.status_code, 400)

    def test_reserved_fields_cannot_be_deleted(self):
        for reserved in ("id", "owner_user_id", "operation_log", "deleted_node_ids"):
            with self.assertRaises(HTTPException) as caught:
                main.apply_canvas_node_operation(
                    main.load_canvas(self.canvas_id),
                    node_fields_payload("generate_ref", {}, [reserved]),
                )
            self.assertEqual(caught.exception.status_code, 400)

    def test_clear_field_list_is_deduplicated_and_capped(self):
        cleaned = main.validate_canvas_node_clear_fields([" manualInputRefs ", "manualInputRefs", "", None])
        self.assertEqual(cleaned, ["manualInputRefs"])
        capped = main.validate_canvas_node_clear_fields([f"field_{index}" for index in range(500)])
        self.assertEqual(len(capped), main.CANVAS_NODE_CLEAR_FIELD_LIMIT)

    def test_missing_node_still_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            main.apply_canvas_node_operation(
                main.load_canvas(self.canvas_id),
                node_fields_payload("generate_missing", {}, ["manualInputRefs"]),
            )
        self.assertEqual(caught.exception.status_code, 404)


class CanvasBoundTaskFieldResurrectionTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end guard for the reported symptom: after the browser removes the
    last reference image, the following bound-task completion broadcast must not
    carry the removed field back into the live canvas."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.canvases = self.data / "canvases"
        self.canvases.mkdir(parents=True, exist_ok=True)
        self.canvas_tasks_file = self.data / "canvas_tasks.json"
        self.patches = [
            patch.object(main, "DATA_DIR", str(self.data)),
            patch.object(main, "CANVAS_DIR", str(self.canvases)),
            patch.object(main, "CANVAS_TASKS_FILE", str(self.canvas_tasks_file)),
        ]
        for item in self.patches:
            item.start()
        main.CANVAS_TASKS.clear()
        main.CANVAS_TASKS_LOADED = False

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        main.CANVAS_TASKS.clear()
        main.CANVAS_TASKS_LOADED = False
        self.temp.cleanup()

    async def test_completion_broadcast_omits_the_removed_reference(self):
        canvas_id = "c-resurrection"
        node_id = "generate_ref"
        task_id = "canvas_img_ref"
        batch_id = "batch-ref"
        main.canvas_task_create({
            "id": task_id,
            "type": "online-image",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "provider_id": "runninghub",
            "model": "gpt-image-2.0/edit",
            "canvas_binding": {"canvas_id": canvas_id, "node_id": node_id, "batch_id": batch_id},
        })
        main.save_canvas({
            "id": canvas_id,
            "kind": "smart",
            "title": "test",
            "nodes": [{
                "id": node_id,
                "type": "smart-image-generation",
                "title": "图片生成",
                "images": [],
                "activeImageIndex": 0,
                "manualInputRefs": [{"url": "/assets/input/removed_ref.png", "kind": "image"}],
                "pendingTasks": [{
                    "taskId": task_id,
                    "kind": "image",
                    "providerId": "runninghub",
                    "model": "gpt-image-2.0/edit",
                    "batchId": batch_id,
                    "serverManaged": True,
                    "logRun": {"nodeId": node_id, "nodeType": "smart-image-generation", "kind": "image", "prompt": "p", "settings": {}, "refs": []},
                    "logStartedAt": int(time.time() * 1000),
                }],
                "pending": 1,
                "running": False,
                "runStartedAt": int(time.time() * 1000),
            }],
            "connections": [],
            "logs": [],
            "viewport": {"x": 0, "y": 0, "scale": 1},
            "deleted_node_ids": [],
            "operation_log": [],
            "sync_revision": 0,
            "updated_at": 0,
        })

        # 1. The browser removes the last manual reference image (and reports the
        #    other field it also dropped) as a narrow deletion operation.
        main.apply_canvas_node_operation(
            main.load_canvas(canvas_id),
            main.CanvasOperationRequest(
                operation_id="op-remove-ref",
                client_id="client-1",
                kind="node_fields",
                node_id=node_id,
                fields={"pending": 1},
                clear_fields=["manualInputRefs"],
            ),
        )

        # 2. The bound task finishes and the server broadcasts the whole node.
        finalized = main.finalize_bound_canvas_task(
            task_id,
            {"images": [{"url": "/assets/output/out.png", "kind": "image", "name": "out.png"}]},
        )
        self.assertIsNotNone(finalized)
        broadcast = AsyncMock()
        with patch.object(main.manager, "broadcast_canvas_operation", broadcast):
            await main.broadcast_bound_canvas_node(task_id, finalized)

        node_updates = [
            call.args[1]
            for call in broadcast.await_args_list
            if call.args[1].get("kind") == "node_fields"
        ]
        self.assertEqual(len(node_updates), 1)
        fields = node_updates[0]["fields"]
        self.assertNotIn(
            "manualInputRefs",
            fields,
            "a removed reference image must not be re-applied by the task-completion broadcast",
        )
        self.assertTrue(any(str(item.get("url") or "") == "/assets/output/out.png" for item in fields["images"]))


class CanvasClientFieldDeletionTests(unittest.TestCase):
    """The client half of the fix must derive deletions from the save diff while
    leaving server-owned task bookkeeping and reserved fields alone."""

    def test_node_field_deletions_only_reports_real_content_removals(self):
        script = "\n".join([
            extract_const("CANVAS_OPERATION_RESERVED_NODE_FIELDS"),
            extract_const("CANVAS_OPERATION_TRANSIENT_NODE_FIELDS"),
            extract_function("nodeFieldDeletions"),
            """
const assert = require('assert');
const previous = {
    id: 'generate_ref',
    manualInputRefs: [{url: '/assets/input/removed.png'}],
    runInputRefs: [{url: '/assets/input/removed.png'}],
    lastRunError: '旧失败信息',
    w: 401.6,
    // Server-owned / serializer-normalized transient state must never be treated
    // as a user deletion.
    pendingTasks: [{taskId: 't1'}],
    submitting: true,
    owner_user_id: 'u1',
    operation_log: [],
};
const node = {id: 'generate_ref', images: [{url: '/assets/output/a.png'}], running: false};
assert.deepStrictEqual(
    nodeFieldDeletions(node, previous).sort(),
    ['lastRunError', 'manualInputRefs', 'runInputRefs', 'w'],
);
// Newly added or untouched fields are not deletions.
assert.deepStrictEqual(nodeFieldDeletions({...node, activeImageIndex: 1}, node), []);
assert.deepStrictEqual(nodeFieldDeletions({...node, manualInputRefs: []}, {...node, manualInputRefs: []}), []);
assert.deepStrictEqual(nodeFieldDeletions(null, previous), []);
assert.deepStrictEqual(nodeFieldDeletions(node, null), []);
console.log('ok');
""",
        ])
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
