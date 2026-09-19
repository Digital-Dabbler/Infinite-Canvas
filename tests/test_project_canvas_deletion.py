"""Deleting a project must not silently re-home its canvases when the user asked
to delete them together.

The reported behaviour was: delete a project, and its canvases reappear in the
Default project instead of being deleted.  ``DELETE /api/projects/{id}`` now
takes an explicit ``with_canvases`` flag; the project-delete dialog asks which
one the user wants, and this suite pins both branches plus the invariants that
keep a trashed canvas recoverable rather than orphaned.
"""

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

ROOT = Path(__file__).resolve().parents[1]
CANVAS_LIST_JS = (ROOT / "static" / "js" / "canvas-list.js").read_text(encoding="utf-8")
CANVAS_LIST_HTML = (ROOT / "static" / "canvas-list.html").read_text(encoding="utf-8")


class ProjectCanvasDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.canvases = self.data / "canvases"
        self.conversations = self.data / "conversations"
        self.previews = self.data / "media_previews"
        for path in (self.canvases, self.conversations, self.previews):
            path.mkdir(parents=True, exist_ok=True)
        self.projects_path = self.data / "projects.json"
        self.user_id = "test-owner"
        self.request = SimpleNamespace(state=SimpleNamespace())
        self.patches = [
            patch.object(main, "DATA_DIR", str(self.data)),
            patch.object(main, "CANVAS_DIR", str(self.canvases)),
            patch.object(main, "CONVERSATION_DIR", str(self.conversations)),
            patch.object(main, "MEDIA_PREVIEW_DIR", str(self.previews)),
            patch.object(main, "PROJECTS_PATH", str(self.projects_path)),
            patch.object(main, "require_authenticated", return_value={"id": self.user_id}),
        ]
        for item in self.patches:
            item.start()
        self.write_projects([
            {"id": "default", "name": "默认项目", "order": 0, "created_at": 1, "updated_at": 1},
            {"id": "proj-a", "name": "项目 A", "order": 1, "created_at": 1, "updated_at": 1},
            {"id": "proj-b", "name": "项目 B", "order": 2, "created_at": 1, "updated_at": 1},
        ])

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    # --- helpers -----------------------------------------------------------

    def write_projects(self, projects):
        self.projects_path.write_text(json.dumps({"projects": projects}, ensure_ascii=False), encoding="utf-8")

    def write_canvas(self, canvas_id, project="proj-a", owner="", editors=None, deleted_at=0, title="t"):
        value = {
            "id": canvas_id,
            "kind": "smart",
            "title": title,
            "owner_user_id": owner,
            "editor_user_ids": list(editors or []),
            "ownership_state": "claimed" if owner else "unclaimed",
            "sharing_version": 1,
            "project": project,
            "nodes": [],
            "connections": [],
            "viewport": {"x": 0, "y": 0, "scale": 1},
            "created_at": 1,
            "updated_at": 1,
        }
        if deleted_at:
            value["deleted_at"] = deleted_at
        (self.canvases / f"{canvas_id}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return value

    def read_canvas(self, canvas_id):
        return json.loads((self.canvases / f"{canvas_id}.json").read_text(encoding="utf-8"))

    def project_ids(self):
        data = json.loads(self.projects_path.read_text(encoding="utf-8"))
        return sorted(p["id"] for p in data["projects"])

    async def delete(self, project_id, **kwargs):
        return await main.delete_project(project_id, self.request, **kwargs)

    # --- default branch: keep the canvases ---------------------------------

    async def test_default_still_only_moves_canvases_back_to_default_project(self):
        """Backward compatibility: omitting the flag keeps the old behaviour."""
        self.write_canvas("c-kept", project="proj-a", owner=self.user_id)
        self.write_canvas("c-other", project="proj-b", owner=self.user_id)

        result = await self.delete("proj-a")

        self.assertEqual(result, {"ok": True, "moved": 1, "trashed": 0, "skipped": 0})
        kept = self.read_canvas("c-kept")
        self.assertEqual(kept["project"], main.DEFAULT_PROJECT_ID)
        self.assertFalse(kept.get("deleted_at"))
        self.assertEqual(self.read_canvas("c-other")["project"], "proj-b")
        self.assertNotIn("proj-a", self.project_ids())
        self.assertIn("c-kept", [c["id"] for c in main.list_canvases()])

    def test_with_canvases_is_opt_in(self):
        """The destructive branch must never be the default."""
        parameter = inspect.signature(main.delete_project).parameters["with_canvases"]
        self.assertIs(parameter.default, False)

    # --- opt-in branch: trash the canvases too -----------------------------

    async def test_with_canvases_trashes_owned_and_editable_canvases(self):
        self.write_canvas("c-owned", project="proj-a", owner=self.user_id)
        self.write_canvas("c-editable", project="proj-a", editors=[self.user_id])
        self.write_canvas("c-other", project="proj-b", owner=self.user_id)

        result = await self.delete("proj-a", with_canvases=True)

        self.assertEqual(result["trashed"], 2)
        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["skipped"], 0)
        for canvas_id in ("c-owned", "c-editable"):
            data = self.read_canvas(canvas_id)
            self.assertTrue(data.get("deleted_at"))
            # Re-homed so a later restore lands somewhere visible instead of
            # pointing at the project row we just removed.
            self.assertEqual(data["project"], main.DEFAULT_PROJECT_ID)
        self.assertFalse(self.read_canvas("c-other").get("deleted_at"))
        self.assertEqual(self.read_canvas("c-other")["project"], "proj-b")

        live_ids = [c["id"] for c in main.list_canvases()]
        self.assertNotIn("c-owned", live_ids)
        self.assertNotIn("c-editable", live_ids)
        trashed_ids = [c["id"] for c in main.list_deleted_canvases()]
        self.assertIn("c-owned", trashed_ids)
        self.assertIn("c-editable", trashed_ids)

    async def test_read_only_canvas_is_kept_instead_of_trashed(self):
        """A project is shared, so bulk deletion must not delete other people's work."""
        self.write_canvas("c-readonly", project="proj-a", owner="someone-else")
        self.write_canvas("c-unclaimed", project="proj-a")

        result = await self.delete("proj-a", with_canvases=True)

        self.assertEqual(result["trashed"], 0)
        self.assertEqual(result["skipped"], 2)
        for canvas_id in ("c-readonly", "c-unclaimed"):
            data = self.read_canvas(canvas_id)
            self.assertFalse(data.get("deleted_at"))
            self.assertEqual(data["project"], main.DEFAULT_PROJECT_ID)

    async def test_already_trashed_canvas_keeps_its_original_timestamp(self):
        self.write_canvas("c-trashed", project="proj-a", owner=self.user_id, deleted_at=1234)

        result = await self.delete("proj-a", with_canvases=True)

        self.assertEqual(result["trashed"], 0)
        self.assertEqual(result["skipped"], 0)
        data = self.read_canvas("c-trashed")
        self.assertEqual(data["deleted_at"], 1234)
        self.assertEqual(data["project"], main.DEFAULT_PROJECT_ID)

    async def test_restored_canvas_is_visible_in_default_project(self):
        """Regression guard for the orphan bug: a canvas pointing at a deleted
        project was restored into no project at all and never rendered again."""
        self.write_canvas("c-restore", project="proj-a", owner=self.user_id)
        await self.delete("proj-a", with_canvases=True)

        restored = await main.restore_canvas("c-restore", self.request)

        self.assertEqual(restored["canvas"]["project"], main.DEFAULT_PROJECT_ID)
        listed = {c["id"]: c for c in main.list_canvases()}
        self.assertIn("c-restore", listed)
        self.assertEqual(listed["c-restore"]["project"], main.DEFAULT_PROJECT_ID)

    # --- guards ------------------------------------------------------------

    async def test_default_project_cannot_be_deleted(self):
        with self.assertRaises(HTTPException) as caught:
            await self.delete(main.DEFAULT_PROJECT_ID, with_canvases=True)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn(main.DEFAULT_PROJECT_ID, self.project_ids())

    async def test_unknown_project_is_not_found(self):
        with self.assertRaises(HTTPException) as caught:
            await self.delete("proj-missing", with_canvases=True)
        self.assertEqual(caught.exception.status_code, 404)

    async def test_delete_requires_an_authenticated_request(self):
        """The project file is global, so the user identity must be resolved even
        though the middleware already rejects anonymous traffic."""
        source = inspect.getsource(main.delete_project)
        self.assertIn("require_authenticated(request)", source)
        self.assertIn("canvas_access_role(data, user)", source)

    # --- frontend contract -------------------------------------------------

    def test_frontend_asks_the_user_through_a_modal(self):
        self.assertIn('id="projectDeleteModal"', CANVAS_LIST_HTML)
        self.assertIn('id="projectDeleteWithCanvases"', CANVAS_LIST_HTML)
        self.assertIn('id="projectDeleteKeep"', CANVAS_LIST_HTML)
        self.assertIn('id="projectDeleteCancel"', CANVAS_LIST_HTML)
        self.assertIn("openProjectDeleteDialog", CANVAS_LIST_JS)
        self.assertIn("confirmDeleteProject", CANVAS_LIST_JS)
        self.assertIn("with_canvases=true", CANVAS_LIST_JS)
        # The sidebar inline confirm was replaced by the modal.
        self.assertNotIn("ws-project-confirm", CANVAS_LIST_JS)


if __name__ == "__main__":
    unittest.main()
