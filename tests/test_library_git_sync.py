"""The admin sync action: migrate runtime publications, then commit the library.

These tests run real Git in a temporary repository, because the behaviour that
matters (pathspec commits leaving unrelated staged work alone, no empty commit,
readable failures outside a repository) only exists against a real repository.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main  # noqa: E402


GIT = shutil.which("git")

LIBRARY_TRACKED_PATHS = (
    "static/data/prompt-library-published",
    "static/data/prompt-library-withdrawn",
    "static/images/prompt-library/published",
    "static/data/workflow-library-published",
    "static/data/workflow-library-withdrawn",
    "static/images/workflow-library/published",
    "static/workflow-library/published",
)

PROMPT_RUNTIME_ID = "published_runtime_prompt"
WORKFLOW_RUNTIME_ID = "published_runtime_workflow"


def write_workflow_zip(path, resource_name="resources/ref.png", size=(1500, 1000)):
    image = BytesIO()
    Image.new("RGB", size, (200, 60, 30)).save(image, "PNG")
    workflow = {
        "format": "infinite-canvas-workflow",
        "version": 1,
        "nodes": [{"id": "n1", "type": "smart-image-generation", "imageUrl": "/assets/ref.png"}],
        "connections": [],
        "resources": [{"url": "/assets/ref.png", "archive": resource_name, "name": "ref.png", "size": 0}],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(resource_name, image.getvalue())
        archive.writestr("workflow.json", json.dumps(workflow))


@unittest.skipIf(not GIT, "git is not installed")
class LibraryGitSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name
        self.git("init", "-q")
        # A local identity keeps the commit deterministic on machines with no
        # global Git configuration.
        self.git("config", "user.email", "library-sync@example.invalid")
        self.git("config", "user.name", "Library Sync Test")

        self.static_data = os.path.join(self.root, "static", "data")
        self.assets_dir = os.path.join(self.root, "assets")
        self.prompt_published_dir = os.path.join(self.static_data, "prompt-library-published")
        self.prompt_withdrawn_dir = os.path.join(self.static_data, "prompt-library-withdrawn")
        self.prompt_cover_dir = os.path.join(self.root, "static", "images", "prompt-library", "published")
        self.workflow_published_dir = os.path.join(self.static_data, "workflow-library-published")
        self.workflow_withdrawn_dir = os.path.join(self.static_data, "workflow-library-withdrawn")
        self.workflow_archive_dir = os.path.join(self.root, "static", "workflow-library", "published")
        self.workflow_cover_dir = os.path.join(self.root, "static", "images", "workflow-library", "published")
        self.workflow_private_dir = os.path.join(self.assets_dir, "workflow_library", "private")
        self.uploads_dir = os.path.join(self.assets_dir, "uploads")
        for directory in (
            self.prompt_published_dir, self.prompt_withdrawn_dir, self.prompt_cover_dir,
            self.workflow_published_dir, self.workflow_withdrawn_dir, self.workflow_archive_dir,
            self.workflow_cover_dir, self.workflow_private_dir, self.uploads_dir,
        ):
            os.makedirs(directory, exist_ok=True)

        self.admin = {"id": "admin", "username": "Admin", "role": "admin"}
        self.patchers = [
            patch.object(main, "BASE_DIR", self.root),
            patch.object(main, "LIBRARY_TRACKED_PATHS", LIBRARY_TRACKED_PATHS),
            patch.object(main, "ASSETS_DIR", self.assets_dir),
            patch.object(main, "STATIC_DIR", os.path.join(self.root, "static")),
            patch.object(main, "PROMPT_LIBRARY_PATH", os.path.join(self.static_data, "prompt_libraries.json")),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_DIR", self.prompt_published_dir),
            patch.object(main, "PROMPT_LIBRARY_WITHDRAWN_DIR", self.prompt_withdrawn_dir),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_COVER_DIR", self.prompt_cover_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PATH", os.path.join(self.static_data, "workflow_library.json")),
            patch.object(main, "WORKFLOW_TRASH_PATH", os.path.join(self.static_data, "workflow_trash.json")),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLISHED_DIR", self.workflow_published_dir),
            patch.object(main, "WORKFLOW_LIBRARY_WITHDRAWN_DIR", self.workflow_withdrawn_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLISHED_ARCHIVE_DIR", self.workflow_archive_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLISHED_COVER_DIR", self.workflow_cover_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PRIVATE_DIR", self.workflow_private_dir),
            patch.object(main, "require_admin", return_value=self.admin),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    # --- helpers -----------------------------------------------------------

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)

    def sync(self):
        return asyncio.run(main.admin_library_sync(object()))

    def status(self):
        return asyncio.run(main.admin_library_sync_status(object()))

    def seed_prompt_runtime_publication(self, cover="/assets/uploads/alice_cover.png"):
        if cover:
            Image.new("RGB", (1400, 900), (15, 90, 180)).save(
                os.path.join(self.uploads_dir, "alice_cover.png"))
        main.save_prompt_libraries({
            "active_library_id": "system",
            "libraries": [],
            "published": [{
                "id": PROMPT_RUNTIME_ID,
                "name": "遗留提示词发布",
                "prefix": "legacy prefix",
                "source_prompt_id": "alice_prompt",
                "source_author_id": "alice",
                "owner_type": "user",
                "owner_id": "alice",
                "cover_url": cover,
                "published": True,
                "published_at": 1700000000000,
            }],
        })

    def seed_workflow_runtime_publication(self):
        write_workflow_zip(os.path.join(self.workflow_private_dir, "workflow_alice.zip"))
        Image.new("RGB", (1400, 900), (180, 90, 15)).save(
            os.path.join(self.workflow_private_dir, "workflow_alice_cover.png"))
        main.save_workflow_library({
            "version": 1,
            "workflows": [{
                "id": "workflow_alice",
                "name": "Alice 工作流",
                "owner_id": "alice",
                "owner_name": "Alice",
                "archive_url": "/assets/workflow_library/private/workflow_alice.zip",
                "cover_url": "/assets/workflow_library/private/workflow_alice_cover.png",
                "node_count": 2,
                "connection_count": 1,
                "resource_count": 1,
                "created_at": 1700000000000,
                "updated_at": 1700000000000,
            }],
            "published": [{
                "id": WORKFLOW_RUNTIME_ID,
                "name": "遗留工作流发布",
                "owner_id": "alice",
                "owner_name": "Alice",
                "source_workflow_id": "workflow_alice",
                # The public copy is gone from this machine, which is exactly the
                # state migration has to recover from the private source.
                "archive_url": f"/assets/workflow_library/public/{WORKFLOW_RUNTIME_ID}.zip",
                "cover_url": f"/assets/workflow_library/public/{WORKFLOW_RUNTIME_ID}_cover.png",
                "node_count": 2,
                "connection_count": 1,
                "resource_count": 1,
                "created_at": 1700000000000,
                "updated_at": 1700000000000,
                "published_at": 1700000000000,
            }],
        })

    # --- migration ---------------------------------------------------------

    def test_migration_moves_runtime_publications_into_the_tracked_layout(self):
        self.seed_prompt_runtime_publication()
        self.seed_workflow_runtime_publication()

        result = self.sync()

        self.assertTrue(os.path.isfile(os.path.join(self.prompt_published_dir, f"{PROMPT_RUNTIME_ID}.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.workflow_published_dir, f"{WORKFLOW_RUNTIME_ID}.json")))
        # Both runtime arrays are consumed, so a publication has one home.
        self.assertEqual(main.load_prompt_libraries()["published"], [])
        self.assertEqual(main.load_workflow_library()["published"], [])
        self.assertEqual(result["migrated"]["prompt"], 1)
        self.assertEqual(result["migrated"]["workflow"], 1)
        self.assertTrue(result["changed"])
        self.assertTrue(result["commit"])

    def test_migration_re_encodes_covers_and_recovers_the_workflow_package(self):
        self.seed_prompt_runtime_publication()
        self.seed_workflow_runtime_publication()

        self.sync()

        prompt_record = main._read_json_file(
            os.path.join(self.prompt_published_dir, f"{PROMPT_RUNTIME_ID}.json"), {})
        self.assertTrue(prompt_record["cover_url"].endswith("_cover.webp"))
        prompt_cover = main.tracked_static_url_file(prompt_record["cover_url"])
        self.assertTrue(prompt_cover and os.path.isfile(prompt_cover))
        with Image.open(prompt_cover) as stored:
            self.assertEqual(stored.format, "WEBP")
            self.assertLessEqual(max(stored.size), main.LIBRARY_COVER_MAX_EDGE)

        workflow_record = main._read_json_file(
            os.path.join(self.workflow_published_dir, f"{WORKFLOW_RUNTIME_ID}.json"), {})
        archive = main.workflow_file_path(workflow_record["archive_url"])
        self.assertTrue(archive and os.path.isfile(archive))
        self.assertTrue(os.path.abspath(archive).startswith(os.path.abspath(self.workflow_archive_dir) + os.sep))
        with zipfile.ZipFile(archive) as package:
            self.assertIn("workflow.json", package.namelist())
        cover = main.workflow_file_path(workflow_record["cover_url"])
        self.assertTrue(cover and os.path.isfile(cover))

    def test_migration_writes_an_empty_cover_when_nothing_resolves(self):
        self.seed_prompt_runtime_publication(cover="/assets/uploads/missing_cover.png")

        self.sync()

        record = main._read_json_file(
            os.path.join(self.prompt_published_dir, f"{PROMPT_RUNTIME_ID}.json"), None)
        self.assertIsNotNone(record)
        self.assertEqual(record["cover_url"], "")

    def test_migration_purges_a_runtime_record_that_has_a_tombstone(self):
        self.seed_prompt_runtime_publication()
        self.seed_workflow_runtime_publication()
        for directory in (self.prompt_withdrawn_dir, self.workflow_withdrawn_dir):
            os.makedirs(directory, exist_ok=True)
        main._write_json_atomic(os.path.join(self.prompt_withdrawn_dir, f"{PROMPT_RUNTIME_ID}.json"),
                                {"id": PROMPT_RUNTIME_ID})
        main._write_json_atomic(os.path.join(self.workflow_withdrawn_dir, f"{WORKFLOW_RUNTIME_ID}.json"),
                                {"id": WORKFLOW_RUNTIME_ID})

        result = self.sync()

        self.assertEqual(main.load_prompt_libraries()["published"], [])
        self.assertEqual(main.load_workflow_library()["published"], [])
        self.assertEqual(result["migrated"]["prompt"], 0)
        self.assertEqual(result["migrated"]["workflow"], 0)
        self.assertEqual(result["migrated"]["prompt_withdrawn"], 1)
        self.assertEqual(result["migrated"]["workflow_withdrawn"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.prompt_published_dir, f"{PROMPT_RUNTIME_ID}.json")))
        self.assertFalse(os.path.exists(os.path.join(self.workflow_published_dir, f"{WORKFLOW_RUNTIME_ID}.json")))
        _, visible = main.merged_published_prompt_snapshots(main.load_prompt_libraries())
        self.assertNotIn(PROMPT_RUNTIME_ID, {item["id"] for item in visible})

    def test_migration_is_idempotent(self):
        self.seed_prompt_runtime_publication()
        self.sync()
        first = self.git("rev-parse", "HEAD").stdout.strip()

        result = self.sync()

        self.assertFalse(result["changed"])
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), first)

    # --- commit semantics ---------------------------------------------------

    def test_commit_leaves_unrelated_staged_content_alone(self):
        self.seed_prompt_runtime_publication()
        unrelated = os.path.join(self.root, "notes.txt")
        with open(unrelated, "w", encoding="utf-8") as handle:
            handle.write("unrelated work in progress\n")
        self.git("add", "--", "notes.txt")

        result = self.sync()

        self.assertTrue(result["changed"])
        staged = self.git("diff", "--cached", "--name-only").stdout
        self.assertIn("notes.txt", staged)
        committed = self.git("show", "--name-only", "--format=", "HEAD").stdout
        self.assertNotIn("notes.txt", committed)
        self.assertIn("static/data/prompt-library-published", committed)

    def test_no_change_produces_no_commit(self):
        before = self.git("rev-parse", "HEAD")
        self.assertNotEqual(before.returncode, 0, "the fixture repository starts with no commit")

        result = self.sync()

        self.assertFalse(result["changed"])
        self.assertEqual(result["commit"], "")
        self.assertEqual(self.git("rev-parse", "HEAD").returncode, 128)

    # --- failure reporting --------------------------------------------------

    def test_reports_a_readable_error_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as plain_dir:
            with patch.object(main, "BASE_DIR", plain_dir):
                with self.assertRaises(main.HTTPException) as error:
                    self.sync()

        self.assertEqual(error.exception.status_code, 409)
        self.assertTrue(error.exception.detail)

    def test_reports_a_readable_error_when_git_is_missing(self):
        with patch.object(main.subprocess, "run", side_effect=FileNotFoundError):
            state = main.library_git_state()
            with self.assertRaises(main.HTTPException) as error:
                self.sync()

        self.assertFalse(state["available"])
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("git", error.exception.detail)

    # --- status report ------------------------------------------------------

    def test_status_reports_pending_records_and_missing_assets(self):
        self.seed_prompt_runtime_publication()
        self.seed_workflow_runtime_publication()

        pending = self.status()
        self.assertEqual(pending["pending"]["prompt"], 1)
        self.assertEqual(pending["pending"]["workflow"], 1)
        self.assertTrue(pending["git"]["repository"])
        self.assertEqual(pending["missing_total"], 0)

        self.sync()
        # Remove a committed cover the way an incomplete commit would leave it.
        record = main._read_json_file(
            os.path.join(self.prompt_published_dir, f"{PROMPT_RUNTIME_ID}.json"), {})
        os.remove(main.tracked_static_url_file(record["cover_url"]))

        after = self.status()
        self.assertEqual(after["pending"]["prompt"], 0)
        self.assertGreaterEqual(after["missing_total"], 1)
        self.assertTrue(any(item["kind"] == "prompt" for item in after["missing"]))


if __name__ == "__main__":
    unittest.main()
