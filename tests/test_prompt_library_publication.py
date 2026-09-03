import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class PromptLibraryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prompt_path = os.path.join(self.temp_dir.name, "prompt_libraries.json")
        self.trash_path = os.path.join(self.temp_dir.name, "asset_trash.json")
        self.public_dir = os.path.join(self.temp_dir.name, "public")
        self.versioned_path = os.path.join(self.temp_dir.name, "prompt-library-published.json")
        self.versioned_cover_dir = os.path.join(self.temp_dir.name, "published-covers")
        self.users = {
            "users": [
                {"id": "alice", "username": "Alice"},
                {"id": "bob", "username": "Bob"},
            ]
        }
        self.patchers = [
            patch.object(main, "PROMPT_LIBRARY_PATH", self.prompt_path),
            patch.object(main, "PROMPT_LIBRARY_PUBLIC_DIR", self.public_dir),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_PATH", self.versioned_path),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_COVER_DIR", self.versioned_cover_dir),
            patch.object(main, "ASSET_TRASH_PATH", self.trash_path),
            patch.object(main, "load_auth_users", return_value=self.users),
            patch.object(main, "cancel_queued_asset_tasks_for_target"),
        ]
        for patcher in self.patchers:
            patcher.start()

        self.alice = {"id": "alice", "username": "Alice", "role": "user"}
        self.bob = {"id": "bob", "username": "Bob", "role": "user"}
        self.data = {
            "active_library_id": "system",
            "libraries": [
                {
                    "id": "prompt_alice",
                    "name": "Alice 的提示词库",
                    "personal": True,
                    "owner_type": "user",
                    "owner_id": "alice",
                    "items": [{
                        "id": "alice_prompt",
                        "name": "Alice 私有提示词",
                        "prefix": "alice prefix",
                        "owner_type": "user",
                        "owner_id": "alice",
                    }],
                    "categories": [],
                },
                {
                    "id": "prompt_bob",
                    "name": "Bob 的提示词库",
                    "personal": True,
                    "owner_type": "user",
                    "owner_id": "bob",
                    "items": [{
                        "id": "bob_prompt",
                        "name": "Bob 私有提示词",
                        "prefix": "bob prefix",
                        "owner_type": "user",
                        "owner_id": "bob",
                    }],
                    "categories": [],
                },
            ],
            "published": [],
        }
        main.save_prompt_libraries(self.data)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_private_libraries_are_not_returned_to_other_regular_users(self):
        view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), self.alice)

        library_ids = {library["id"] for library in view["libraries"]}
        returned_item_ids = {item["id"] for library in view["libraries"] for item in library["items"]}
        self.assertIn("prompt_alice", library_ids)
        self.assertNotIn("prompt_bob", library_ids)
        self.assertIn("alice_prompt", returned_item_ids)
        self.assertNotIn("bob_prompt", returned_item_ids)

    def test_publish_creates_independent_public_snapshot_and_withdraw_removes_it(self):
        with patch.object(main, "require_authenticated", return_value=self.alice):
            result = asyncio.run(main.publish_prompt_library_item(
                "alice_prompt", main.PromptLibraryPublishRequest(published=True), object()
            ))

        snapshot = result["snapshot"]
        self.assertTrue(snapshot["published"])
        self.assertEqual(snapshot["source_prompt_id"], "alice_prompt")
        self.assertNotEqual(snapshot["id"], "alice_prompt")

        bob_view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), self.bob)
        self.assertEqual([item["id"] for item in bob_view["published"]], [])
        self.assertIn(snapshot["id"], {item["id"] for item in bob_view["inspiration"]})

        with patch.object(main, "require_authenticated", return_value=self.alice):
            asyncio.run(main.delete_prompt_library_item("alice_prompt", object()))

        bob_view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), self.bob)
        self.assertIn(snapshot["id"], {item["id"] for item in bob_view["inspiration"]})

        with patch.object(main, "require_authenticated", return_value=self.alice):
            withdrawn = asyncio.run(main.withdraw_prompt_library_snapshot(snapshot["id"], object()))

        self.assertTrue(withdrawn["withdrawn"])
        bob_view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), self.bob)
        self.assertNotIn(snapshot["id"], {item["id"] for item in bob_view["inspiration"]})

    def test_published_snapshot_is_stored_in_the_versioned_catalog_not_runtime_data(self):
        with patch.object(main, "require_authenticated", return_value=self.alice):
            result = asyncio.run(main.publish_prompt_library_item(
                "alice_prompt", main.PromptLibraryPublishRequest(published=True), object()
            ))

        self.assertTrue(os.path.isfile(self.versioned_path))
        versioned = main.load_versioned_published_prompts()
        self.assertEqual([item["id"] for item in versioned], [result["snapshot"]["id"]])
        self.assertEqual(main.load_prompt_libraries()["published"], [])

    def test_published_cover_is_copied_to_the_versioned_static_directory(self):
        source = os.path.join(self.temp_dir.name, "cover.png")
        with open(source, "wb") as handle:
            handle.write(b"png")
        with patch.object(main, "output_file_from_url", return_value=source), \
             patch.object(main, "content_type_for_path", return_value="image/png"):
            url = main.prompt_public_cover_copy("/assets/uploads/cover.png", "published_example")

        self.assertEqual(url, "/static/images/prompt-library/published/published_example_cover.png")
        cover_path = os.path.join(self.versioned_cover_dir, "published_example_cover.png")
        self.assertTrue(os.path.isfile(cover_path))
        main.remove_prompt_public_cover({"cover_url": url})
        self.assertFalse(os.path.exists(cover_path))

    def test_admin_my_publications_excludes_other_users_snapshots(self):
        with patch.object(main, "require_authenticated", return_value=self.alice):
            result = asyncio.run(main.publish_prompt_library_item(
                "alice_prompt", main.PromptLibraryPublishRequest(published=True), object()
            ))

        admin = {"id": "admin", "username": "Admin", "role": "admin"}
        admin_view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), admin)
        self.assertEqual(admin_view["published"], [])
        self.assertIn(result["snapshot"]["id"], {item["id"] for item in admin_view["inspiration"]})

    def test_publish_configuration_overrides_only_the_public_snapshot(self):
        source = self.data["libraries"][0]["items"][0]
        source.update({"category": "style", "subcategory": "real"})
        main.save_prompt_libraries(self.data)

        with patch.object(main, "require_authenticated", return_value=self.alice):
            result = asyncio.run(main.publish_prompt_library_item(
                "alice_prompt",
                main.PromptLibraryPublishRequest(name="公开电影肖像", category="filter", subcategory="color"),
                object(),
            ))

        snapshot = result["snapshot"]
        self.assertEqual(snapshot["name"], "公开电影肖像")
        self.assertEqual(snapshot["category"], "filter")
        self.assertEqual(snapshot["subcategory"], "color")

        stored_library = next(library for library in main.load_prompt_libraries()["libraries"] if library["id"] == "prompt_alice")
        stored_source = stored_library["items"][0]
        self.assertEqual(stored_source["name"], "Alice 私有提示词")
        self.assertEqual(stored_source["category"], "style")
        self.assertEqual(stored_source["subcategory"], "real")

    def test_publish_rejects_empty_name_and_unknown_category(self):
        with patch.object(main, "require_authenticated", return_value=self.alice):
            with self.assertRaises(main.HTTPException) as empty_name:
                asyncio.run(main.publish_prompt_library_item(
                    "alice_prompt", main.PromptLibraryPublishRequest(name=" "), object()
                ))
            self.assertEqual(empty_name.exception.status_code, 400)

            with self.assertRaises(main.HTTPException) as invalid_category:
                asyncio.run(main.publish_prompt_library_item(
                    "alice_prompt", main.PromptLibraryPublishRequest(category="custom"), object()
                ))
            self.assertEqual(invalid_category.exception.status_code, 400)

            with self.assertRaises(main.HTTPException) as invalid_subcategory:
                asyncio.run(main.publish_prompt_library_item(
                    "alice_prompt", main.PromptLibraryPublishRequest(category="style", subcategory="film"), object()
                ))
            self.assertEqual(invalid_subcategory.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
