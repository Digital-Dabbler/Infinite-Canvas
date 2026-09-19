import asyncio
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


ROOT = Path(__file__).resolve().parents[1]


class WorkflowLibraryFrontendContractTests(unittest.TestCase):
    """The workflow-library tab keys must stay in sync with the modal markup.

    The 我的发布 tab rendered "someone else's item" cards, because the script
    compared the tab against a mangled key that never matched data-tab.
    """

    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "static/js/workflow-library.js").read_text(encoding="utf-8")
        markup = (ROOT / "static/smart-canvas.html").read_text(encoding="utf-8")
        modal = re.search(r'id="workflows-library-overlay".*?<div class="library-content">', markup, re.S)
        cls.tabs = re.findall(r'data-tab="([^"]+)"', modal.group(0))

    def test_modal_tabs_are_all_handled_by_the_script(self):
        self.assertEqual(self.tabs, ["inspiration", "favorites", "myWorkflows", "myPublished"])
        for tab in self.tabs:
            self.assertIn(f"'{tab}'", self.script)

    def test_tab_keys_are_plain_ascii(self):
        self.assertNotRegex(self.script, r"my[\u4e00-\u9fff]")

    def test_published_tab_withdraws_through_the_owner_scoped_endpoint(self):
        self.assertIn("data-wf-action=\"withdraw\"", self.script)
        self.assertIn("library.unpublish", self.script)
        self.assertIn("/api/workflow-library/published/", self.script)
        self.assertIn("source_workflow_id", self.script)


class WorkflowLibraryPublicationTests(unittest.TestCase):
    """Publishing and unpublishing workflows from the workflow library."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = self.temp_dir.name
        self.assets_dir = os.path.join(root, "assets")
        self.private_dir = os.path.join(self.assets_dir, "workflow_library", "private")
        self.public_dir = os.path.join(self.assets_dir, "workflow_library", "public")
        self.uploads_dir = os.path.join(self.assets_dir, "workflows")
        os.makedirs(self.uploads_dir, exist_ok=True)

        self.alice = {"id": "alice", "username": "Alice", "role": "user"}
        self.bob = {"id": "bob", "username": "Bob", "role": "user"}

        self.patchers = [
            patch.object(main, "ASSETS_DIR", self.assets_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PATH", os.path.join(root, "workflow_library.json")),
            patch.object(main, "WORKFLOW_TRASH_PATH", os.path.join(root, "workflow_trash.json")),
            patch.object(main, "WORKFLOW_LIBRARY_PRIVATE_DIR", self.private_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLIC_DIR", self.public_dir),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(self._stop_patchers)

        self.archive_path = os.path.join(self.uploads_dir, "alice_workflow.zip")
        with open(self.archive_path, "wb") as handle:
            handle.write(b"PK\x03\x04alice-workflow")
        self.cover_path = os.path.join(self.uploads_dir, "alice_cover.png")
        with open(self.cover_path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        self.seed_library()

    def _stop_patchers(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def seed_library(self):
        main.save_workflow_library({
            "version": 1,
            "workflows": [
                {
                    "id": "workflow_alice",
                    "name": "Alice 工作流",
                    "owner_id": "alice",
                    "owner_name": "Alice",
                    "archive_url": "/assets/workflows/alice_workflow.zip",
                    "cover_url": "/assets/workflows/alice_cover.png",
                    "node_count": 3,
                    "connection_count": 2,
                    "resource_count": 1,
                    "created_at": 1700000000000,
                    "updated_at": 1700000000000,
                },
                {
                    "id": "workflow_bob",
                    "name": "Bob 工作流",
                    "owner_id": "bob",
                    "owner_name": "Bob",
                    "archive_url": "/assets/workflows/alice_workflow.zip",
                    "cover_url": "",
                    "node_count": 1,
                    "connection_count": 0,
                    "resource_count": 0,
                    "created_at": 1700000000000,
                    "updated_at": 1700000000000,
                },
            ],
            "published": [],
        })

    def publish(self, user, workflow_id="workflow_alice"):
        with patch.object(main, "require_authenticated", return_value=user):
            return asyncio.run(main.publish_workflow_library_item(
                workflow_id, main.WorkflowLibraryPublishRequest(published=True), object()
            ))

    def set_published_flag(self, user, workflow_id, published):
        with patch.object(main, "require_authenticated", return_value=user):
            return asyncio.run(main.publish_workflow_library_item(
                workflow_id, main.WorkflowLibraryPublishRequest(published=published), object()
            ))

    def withdraw(self, user, snapshot_id):
        with patch.object(main, "require_authenticated", return_value=user):
            return asyncio.run(main.withdraw_workflow_library_snapshot(snapshot_id, object()))

    def view(self, user):
        return main.workflow_public_view(main.load_workflow_library(), user)

    def stored_snapshot_path(self, snapshot):
        return main.output_file_from_url(snapshot["archive_url"])

    def test_publish_creates_independent_snapshot_in_public_directory(self):
        result = self.publish(self.alice)

        snapshot = result["snapshot"]
        self.assertTrue(result["published"])
        self.assertEqual(snapshot["source_workflow_id"], "workflow_alice")
        self.assertNotEqual(snapshot["id"], "workflow_alice")
        self.assertEqual(snapshot["owner_id"], "alice")
        self.assertTrue(snapshot["published_at"])

        snapshot_path = self.stored_snapshot_path(snapshot)
        self.assertTrue(os.path.isfile(snapshot_path))
        self.assertTrue(os.path.abspath(snapshot_path).startswith(os.path.abspath(self.public_dir) + os.sep))
        self.assertNotEqual(os.path.abspath(snapshot_path), os.path.abspath(self.archive_path))
        cover_path = main.output_file_from_url(snapshot["cover_url"])
        self.assertTrue(cover_path and os.path.isfile(cover_path))
        self.assertTrue(os.path.abspath(cover_path).startswith(os.path.abspath(self.public_dir) + os.sep))

        # The source workflow keeps its own private archive and is untouched.
        source = next(item for item in self.view(self.alice)["workflows"] if item["id"] == "workflow_alice")
        self.assertEqual(source["archive_url"], "/assets/workflows/alice_workflow.zip")
        self.assertTrue(os.path.isfile(self.archive_path))

    def test_published_snapshot_is_visible_to_others_only_in_inspiration(self):
        snapshot = self.publish(self.alice)["snapshot"]

        alice_view = self.view(self.alice)
        self.assertEqual([item["id"] for item in alice_view["published"]], [snapshot["id"]])
        self.assertIn(snapshot["id"], {item["id"] for item in alice_view["inspiration"]})

        bob_view = self.view(self.bob)
        self.assertEqual(bob_view["published"], [])
        self.assertIn(snapshot["id"], {item["id"] for item in bob_view["inspiration"]})

    def test_withdraw_removes_the_snapshot_and_its_files(self):
        snapshot = self.publish(self.alice)["snapshot"]
        snapshot_path = self.stored_snapshot_path(snapshot)
        cover_path = main.output_file_from_url(snapshot["cover_url"])
        self.assertTrue(os.path.isfile(snapshot_path))
        self.assertTrue(os.path.isfile(cover_path))

        result = self.withdraw(self.alice, snapshot["id"])

        self.assertTrue(result["withdrawn"])
        self.assertEqual(main.load_workflow_library()["published"], [])
        self.assertFalse(os.path.exists(snapshot_path))
        self.assertFalse(os.path.exists(cover_path))
        self.assertEqual(result["library"]["published"], [])
        self.assertNotIn(snapshot["id"], {item["id"] for item in self.view(self.bob)["inspiration"]})
        # Withdrawing a publication never deletes the owner's own workflow.
        self.assertIn("workflow_alice", {item["id"] for item in self.view(self.alice)["workflows"]})

    def test_unpublish_flag_removes_the_owners_snapshot(self):
        snapshot = self.publish(self.alice)["snapshot"]
        snapshot_path = self.stored_snapshot_path(snapshot)

        result = self.set_published_flag(self.alice, "workflow_alice", False)

        self.assertFalse(result["published"])
        self.assertNotIn(snapshot["id"], {item["id"] for item in result["library"]["published"]})
        self.assertEqual(main.load_workflow_library()["published"], [])
        self.assertFalse(os.path.exists(snapshot_path))

    def test_unpublish_flag_without_publication_is_a_no_op(self):
        result = self.set_published_flag(self.alice, "workflow_alice", False)

        self.assertFalse(result["published"])
        self.assertEqual(main.load_workflow_library()["published"], [])
        self.assertIn("workflow_alice", {item["id"] for item in self.view(self.alice)["workflows"]})

    def test_republishing_after_withdraw_creates_a_new_snapshot(self):
        first = self.publish(self.alice)["snapshot"]
        self.withdraw(self.alice, first["id"])

        second = self.publish(self.alice)["snapshot"]

        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual([item["id"] for item in self.view(self.alice)["published"]], [second["id"]])

    def test_publishing_an_existing_publication_does_not_duplicate_it(self):
        first = self.publish(self.alice)["snapshot"]

        result = self.publish(self.alice)

        self.assertTrue(result["published"])
        self.assertEqual(result["snapshot"]["id"], first["id"])
        self.assertEqual(len(main.load_workflow_library()["published"]), 1)

    def test_other_users_cannot_publish_or_withdraw_foreign_workflows(self):
        snapshot = self.publish(self.alice)["snapshot"]

        with self.assertRaises(main.HTTPException) as publish_error:
            self.publish(self.bob, "workflow_alice")
        self.assertEqual(publish_error.exception.status_code, 404)

        with self.assertRaises(main.HTTPException) as withdraw_error:
            self.withdraw(self.bob, snapshot["id"])
        self.assertEqual(withdraw_error.exception.status_code, 404)

        self.assertEqual([item["id"] for item in main.load_workflow_library()["published"]], [snapshot["id"]])

    def test_publish_without_archive_is_rejected(self):
        os.remove(self.archive_path)

        with self.assertRaises(main.HTTPException) as error:
            self.publish(self.alice)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(main.load_workflow_library()["published"], [])


if __name__ == "__main__":
    unittest.main()
