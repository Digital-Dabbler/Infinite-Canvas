import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


ROOT = Path(__file__).resolve().parents[1]


def write_workflow_zip(path, resource_name="resources/ref.png", size=(1600, 1200)):
    """Write a valid workflow archive with one oversized image resource.

    The publish path re-encodes image resources inside the archive, so the test
    fixture has to be a real zip with a real image for that to be observable.
    """
    image = BytesIO()
    Image.new("RGB", size, (10, 200, 90)).save(image, "PNG")
    workflow = {
        "format": "infinite-canvas-workflow",
        "version": 1,
        "nodes": [{
            "id": "n1",
            "type": "smart-image-generation",
            "imageUrl": "/assets/workflows/ref.png",
        }],
        "connections": [],
        "resources": [{
            "url": "/assets/workflows/ref.png",
            "archive": resource_name,
            "name": "ref.png",
            "size": 0,
        }],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(resource_name, image.getvalue())
        archive.writestr("workflow.json", json.dumps(workflow))
    return workflow


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
        self.uploads_dir = os.path.join(self.assets_dir, "workflows")
        os.makedirs(self.uploads_dir, exist_ok=True)
        # Published artifacts live in the Git-tracked tree, not under assets/.
        self.published_dir = os.path.join(root, "static", "data", "workflow-library-published")
        self.withdrawn_dir = os.path.join(root, "static", "data", "workflow-library-withdrawn")
        self.published_archive_dir = os.path.join(root, "static", "workflow-library", "published")
        self.published_cover_dir = os.path.join(root, "static", "images", "workflow-library", "published")

        self.alice = {"id": "alice", "username": "Alice", "role": "user"}
        self.bob = {"id": "bob", "username": "Bob", "role": "user"}

        self.patchers = [
            patch.object(main, "ASSETS_DIR", self.assets_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PATH", os.path.join(root, "workflow_library.json")),
            patch.object(main, "WORKFLOW_TRASH_PATH", os.path.join(root, "workflow_trash.json")),
            patch.object(main, "WORKFLOW_LIBRARY_PRIVATE_DIR", self.private_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLISHED_DIR", self.published_dir),
            patch.object(main, "WORKFLOW_LIBRARY_WITHDRAWN_DIR", self.withdrawn_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLISHED_ARCHIVE_DIR", self.published_archive_dir),
            patch.object(main, "WORKFLOW_LIBRARY_PUBLISHED_COVER_DIR", self.published_cover_dir),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(self._stop_patchers)

        self.archive_path = os.path.join(self.uploads_dir, "alice_workflow.zip")
        write_workflow_zip(self.archive_path)
        self.cover_path = os.path.join(self.uploads_dir, "alice_cover.png")
        Image.new("RGB", (1400, 900), (30, 120, 210)).save(self.cover_path)
        self.seed_library()

    def _stop_patchers(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def tracked_snapshots(self):
        return main.published_workflow_snapshots(main.load_workflow_library())

    def tracked_ids(self):
        return [item["id"] for item in self.tracked_snapshots()]

    def tracked_manifest_path(self, snapshot_id):
        return os.path.join(self.published_dir, f"{snapshot_id}.json")

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
        return main.workflow_file_path(snapshot["archive_url"])

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
        self.assertTrue(os.path.abspath(snapshot_path).startswith(os.path.abspath(self.published_archive_dir) + os.sep))
        self.assertNotEqual(os.path.abspath(snapshot_path), os.path.abspath(self.archive_path))
        cover_path = main.workflow_file_path(snapshot["cover_url"])
        self.assertTrue(cover_path and os.path.isfile(cover_path))
        self.assertTrue(os.path.abspath(cover_path).startswith(os.path.abspath(self.published_cover_dir) + os.sep))
        self.assertTrue(os.path.isfile(self.tracked_manifest_path(snapshot["id"])))
        # Publications are tracked files, never runtime records.
        self.assertEqual(main.load_workflow_library()["published"], [])

        # The source workflow keeps its own private archive and is untouched.
        source = next(item for item in self.view(self.alice)["workflows"] if item["id"] == "workflow_alice")
        self.assertEqual(source["archive_url"], "/assets/workflows/alice_workflow.zip")
        self.assertTrue(os.path.isfile(self.archive_path))

    def test_published_archive_re_encodes_image_resources_and_keeps_resource_urls(self):
        snapshot = self.publish(self.alice)["snapshot"]

        with zipfile.ZipFile(self.stored_snapshot_path(snapshot)) as archive:
            workflow = json.loads(archive.read("workflow.json").decode("utf-8"))
            resource = workflow["resources"][0]
            self.assertTrue(resource["archive"].endswith(".webp"))
            self.assertTrue(resource["name"].endswith(".webp"))
            data = archive.read(resource["archive"])
            self.assertGreater(resource["size"], 0)
        # Node references are rewritten through ``url`` on import, so it must not
        # change even though the archived file was renamed and re-encoded.
        self.assertEqual(resource["url"], "/assets/workflows/ref.png")
        with Image.open(BytesIO(data)) as stored:
            self.assertEqual(stored.format, "WEBP")
            self.assertLessEqual(max(stored.size), main.WORKFLOW_RESOURCE_MAX_EDGE)

    def test_published_cover_is_re_encoded_to_webp(self):
        snapshot = self.publish(self.alice)["snapshot"]

        self.assertTrue(snapshot["cover_url"].endswith("_cover.webp"))
        cover_path = main.workflow_file_path(snapshot["cover_url"])
        with Image.open(cover_path) as stored:
            self.assertEqual(stored.format, "WEBP")
            self.assertLessEqual(max(stored.size), main.LIBRARY_COVER_MAX_EDGE)

    def test_apply_and_package_resolve_a_tracked_snapshot_with_no_runtime_copy(self):
        snapshot = self.publish(self.alice)["snapshot"]
        self.assertEqual(main.load_workflow_library()["published"], [])

        with patch.object(main, "require_authenticated", return_value=self.alice):
            package = asyncio.run(main.download_workflow_library_package(snapshot["id"], object()))
            applied = asyncio.run(main.apply_workflow_library_item(snapshot["id"], object()))

        self.assertTrue(os.path.isfile(package.path))
        self.assertIn("nodes", applied)

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
        cover_path = main.workflow_file_path(snapshot["cover_url"])
        self.assertTrue(os.path.isfile(snapshot_path))
        self.assertTrue(os.path.isfile(cover_path))

        result = self.withdraw(self.alice, snapshot["id"])

        self.assertTrue(result["withdrawn"])
        self.assertEqual(self.tracked_ids(), [])
        self.assertFalse(os.path.exists(snapshot_path))
        self.assertFalse(os.path.exists(cover_path))
        self.assertEqual(result["library"]["published"], [])
        self.assertNotIn(snapshot["id"], {item["id"] for item in self.view(self.bob)["inspiration"]})
        # Withdrawing a publication never deletes the owner's own workflow.
        self.assertIn("workflow_alice", {item["id"] for item in self.view(self.alice)["workflows"]})

    def test_withdraw_leaves_a_deterministic_tombstone(self):
        snapshot = self.publish(self.alice)["snapshot"]

        self.withdraw(self.alice, snapshot["id"])

        tombstone = os.path.join(self.withdrawn_dir, f"{snapshot['id']}.json")
        self.assertTrue(os.path.isfile(tombstone))
        with open(tombstone, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), '{\n  "id": "%s"\n}' % snapshot["id"])
        self.assertFalse(os.path.exists(self.tracked_manifest_path(snapshot["id"])))

    def test_tombstone_hides_a_stale_runtime_copy(self):
        snapshot = self.publish(self.alice)["snapshot"]
        self.withdraw(self.alice, snapshot["id"])

        # A machine that has not pulled the withdrawal still holds the runtime
        # record. The tombstone has to keep it out of the served view.
        data = main.load_workflow_library()
        data["published"] = [{
            "id": snapshot["id"],
            "name": snapshot["name"],
            "owner_id": "alice",
            "source_workflow_id": "workflow_alice",
            "archive_url": snapshot["archive_url"],
            "cover_url": snapshot["cover_url"],
            "published_at": snapshot["published_at"],
        }]
        main.save_workflow_library(data)

        self.assertEqual(self.tracked_ids(), [])
        self.assertNotIn(snapshot["id"], {item["id"] for item in self.view(self.bob)["inspiration"]})

    def test_unpublish_flag_removes_the_owners_snapshot(self):
        snapshot = self.publish(self.alice)["snapshot"]
        snapshot_path = self.stored_snapshot_path(snapshot)

        result = self.set_published_flag(self.alice, "workflow_alice", False)

        self.assertFalse(result["published"])
        self.assertNotIn(snapshot["id"], {item["id"] for item in result["library"]["published"]})
        self.assertEqual(self.tracked_ids(), [])
        self.assertFalse(os.path.exists(snapshot_path))

    def test_unpublish_flag_without_publication_is_a_no_op(self):
        result = self.set_published_flag(self.alice, "workflow_alice", False)

        self.assertFalse(result["published"])
        self.assertEqual(self.tracked_ids(), [])
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
        self.assertEqual(self.tracked_ids(), [first["id"]])

    def test_other_users_cannot_publish_or_withdraw_foreign_workflows(self):
        snapshot = self.publish(self.alice)["snapshot"]

        with self.assertRaises(main.HTTPException) as publish_error:
            self.publish(self.bob, "workflow_alice")
        self.assertEqual(publish_error.exception.status_code, 404)

        with self.assertRaises(main.HTTPException) as withdraw_error:
            self.withdraw(self.bob, snapshot["id"])
        self.assertEqual(withdraw_error.exception.status_code, 404)

        self.assertEqual(self.tracked_ids(), [snapshot["id"]])

    def test_publish_without_archive_is_rejected(self):
        os.remove(self.archive_path)

        with self.assertRaises(main.HTTPException) as error:
            self.publish(self.alice)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.tracked_ids(), [])


if __name__ == "__main__":
    unittest.main()
