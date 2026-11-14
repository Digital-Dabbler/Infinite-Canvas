import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main


class CanvasAssetIndexCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.canvas_dir = os.path.join(self.temp_dir.name, "canvases")
        self.cache_path = os.path.join(self.temp_dir.name, "canvas_asset_index_cache.json")
        os.makedirs(self.canvas_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_canvas(self, canvas_id, images):
        path = os.path.join(self.canvas_dir, f"{canvas_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "id": canvas_id,
                    "title": canvas_id,
                    "kind": "smart",
                    "updated_at": len(images),
                    "nodes": [
                        {
                            "id": f"node-{canvas_id}",
                            "type": "smart-image-generation",
                            "images": [{"url": url, "kind": "image"} for url in images],
                        }
                    ],
                },
                handle,
                ensure_ascii=False,
            )
        return path

    def test_unchanged_canvases_reuse_cached_asset_entries(self):
        first_path = self.write_canvas("first", ["/assets/output/first.png"])
        self.write_canvas("second", ["/assets/output/second.png"])

        with (
            patch.object(main, "CANVAS_DIR", self.canvas_dir),
            patch.object(main, "CANVAS_ASSET_INDEX_CACHE_PATH", self.cache_path),
            patch.object(main, "cleanup_expired_canvas_trash", lambda: None),
            patch.object(main, "extract_canvas_assets", wraps=main.extract_canvas_assets) as extractor,
        ):
            first = main.canvas_assets_index()
            second = main.canvas_assets_index()
            self.assertEqual(extractor.call_count, 2)
            self.assertEqual(first, second)

            with open(first_path, "r", encoding="utf-8") as handle:
                changed = json.load(handle)
            changed["nodes"][0]["images"].append(
                {"url": "/assets/output/first-extra.png", "kind": "image"}
            )
            with open(first_path, "w", encoding="utf-8") as handle:
                json.dump(changed, handle, ensure_ascii=False)

            refreshed = main.canvas_assets_index()
            self.assertEqual(extractor.call_count, 3)
            self.assertEqual(len(refreshed["items"]), 3)


class SmartCanvasPerformanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canvas_js = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.shell_js = (ROOT / "static" / "js" / "smart-canvas-shell.js").read_text(encoding="utf-8")
        cls.assets_js = (ROOT / "static" / "js" / "asset-manager.js").read_text(encoding="utf-8")

    def test_grid_snap_requires_explicit_opt_in(self):
        self.assertIn("localStorage.getItem(SNAP_KEY) === '1'", self.shell_js)

    def test_canvas_media_stays_on_bounded_previews(self):
        self.assertIn("画布只使用尺寸受控的预览图", self.canvas_js)
        self.assertIn("if(preview && img.getAttribute('src') !== preview) img.src = preview", self.canvas_js)
        self.assertIn('loading="lazy"', self.canvas_js)
        self.assertIn('decoding="async"', self.canvas_js)

    def test_generation_thumbnail_strip_is_windowed(self):
        self.assertIn("const thumbEnd = Math.min(imgs.length, thumbStart + 9)", self.canvas_js)
        self.assertIn("imgs.slice(thumbStart, thumbEnd)", self.canvas_js)

    def test_asset_manager_initially_loads_only_the_active_tab(self):
        self.assertIn("loadAll({force:false})", self.assets_js)
        self.assertIn("if(activeTab === 'canvas-assets')", self.assets_js)


class CanvasMetaIndexCacheTests(unittest.TestCase):
    """The workspace list must not re-parse every canvas file on each visit."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.canvas_dir = os.path.join(self.data_dir, "canvases")
        os.makedirs(self.canvas_dir, exist_ok=True)
        self.cover_calls = []
        self.conversation_calls = []
        self.record_mock = self.start_patch(
            patch.object(main, "canvas_record", wraps=main.canvas_record)
        )
        self.start_patch(patch.object(main, "DATA_DIR", self.data_dir))
        self.start_patch(patch.object(main, "CANVAS_DIR", self.canvas_dir))
        self.start_patch(
            patch.object(
                main,
                "clear_canvas_cover_file",
                lambda canvas_id: self.cover_calls.append(canvas_id),
            )
        )
        self.start_patch(
            patch.object(
                main,
                "delete_canvas_conversations",
                lambda canvas_id: self.conversation_calls.append(canvas_id),
            )
        )

    def start_patch(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_canvas(self, canvas_id, **fields):
        payload = {
            "id": canvas_id,
            "title": fields.pop("title", canvas_id),
            "kind": "smart",
            "nodes": [],
            "created_at": 1,
            "updated_at": 1,
        }
        payload.update(fields)
        path = os.path.join(self.canvas_dir, f"{canvas_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def test_warm_load_stat_only_and_never_reparses(self):
        self.write_canvas("alpha", title="Alpha")
        self.write_canvas("beta", title="Beta")

        first = main.list_canvases()
        self.assertEqual(self.record_mock.call_count, 2)
        self.assertTrue(os.path.exists(main.canvas_meta_index_cache_path()))

        second = main.list_canvases()
        # A second visit must be served by the persisted index.
        self.assertEqual(self.record_mock.call_count, 2)
        self.assertEqual(first, second)
        self.assertEqual({item["id"] for item in second}, {"alpha", "beta"})

        # /api/projects shares the same index instead of walking the directory again.
        projects = main.list_projects()
        self.assertEqual(self.record_mock.call_count, 2)
        default_project = next(p for p in projects if p["id"] == "default")
        self.assertEqual(default_project["canvas_count"], 2)

    def test_changed_canvas_is_reparsed_and_refreshed(self):
        self.write_canvas("alpha", title="Alpha")
        main.list_canvases()
        self.assertEqual(self.record_mock.call_count, 1)

        self.write_canvas("alpha", title="Alpha renamed", updated_at=99)
        records = main.list_canvases()
        self.assertEqual(self.record_mock.call_count, 2)
        self.assertEqual(records[0]["title"], "Alpha renamed")
        self.assertEqual(records[0]["updated_at"], 99)

        # Unrelated reads stay warm after the refresh.
        main.list_canvases()
        self.assertEqual(self.record_mock.call_count, 2)

    def test_non_smart_files_are_not_listed(self):
        self.write_canvas("smart-one")
        with open(os.path.join(self.canvas_dir, "legacy.json"), "w", encoding="utf-8") as handle:
            json.dump({"id": "legacy", "kind": "legacy", "nodes": []}, handle)

        self.assertEqual([item["id"] for item in main.list_canvases()], ["smart-one"])

    def test_corrupt_canvas_is_skipped_without_rewriting_the_index(self):
        self.write_canvas("good")
        bad_path = os.path.join(self.canvas_dir, "broken.json")
        with open(bad_path, "w", encoding="utf-8") as handle:
            handle.write('{"id": "broken", "kind": "smart",}')

        self.assertEqual([item["id"] for item in main.list_canvases()], ["good"])
        cache_path = main.canvas_meta_index_cache_path()
        first_mtime = os.stat(cache_path).st_mtime_ns

        # A permanently unreadable file must not force a cache write on every load.
        self.assertEqual([item["id"] for item in main.list_canvases()], ["good"])
        self.assertEqual(os.stat(cache_path).st_mtime_ns, first_mtime)
        self.assertEqual(self.record_mock.call_count, 1)

    def test_expired_trash_entry_is_purged_through_the_index(self):
        self.write_canvas("kept")
        self.write_canvas("old-trash", deleted_at=1)
        self.write_canvas("new-trash", deleted_at=main.now_ms() - 1000)

        self.assertEqual([item["id"] for item in main.list_canvases()], ["kept"])
        trashed = {item["id"] for item in main.list_deleted_canvases()}
        self.assertEqual(trashed, {"new-trash"})
        self.assertFalse(os.path.exists(os.path.join(self.canvas_dir, "old-trash.json")))
        self.assertIn("old-trash", self.cover_calls)
        self.assertIn("old-trash", self.conversation_calls)


class CanvasListPageTests(unittest.TestCase):
    """``/api/canvases`` paging contract used by the workspace board."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.canvas_dir = os.path.join(self.data_dir, "canvases")
        os.makedirs(self.canvas_dir, exist_ok=True)
        self.user = {"id": "user-1", "role": "user"}
        for patcher in (
            patch.object(main, "DATA_DIR", self.data_dir),
            patch.object(main, "CANVAS_DIR", self.canvas_dir),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_canvas(self, canvas_id, **fields):
        payload = {"id": canvas_id, "title": canvas_id, "kind": "smart", "nodes": []}
        payload.update(fields)
        with open(os.path.join(self.canvas_dir, f"{canvas_id}.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return payload

    def run_page(self, **kwargs):
        return main.canvas_list_page(self.user, **kwargs)

    def test_project_filter_sorting_and_paging(self):
        self.write_canvas("a-old", project="p1", updated_at=10)
        self.write_canvas("a-new", project="p1", updated_at=30)
        self.write_canvas("a-pin", project="p1", updated_at=1, pinned=True)
        self.write_canvas("b-one", project="p2", updated_at=20)

        first = self.run_page(project_id="p1", limit=2, offset=0)
        self.assertEqual(first["total"], 3)
        self.assertEqual([item["id"] for item in first["canvases"]], ["a-pin", "a-new"])
        self.assertTrue(first["has_more"])

        second = self.run_page(project_id="p1", limit=2, offset=2)
        self.assertEqual([item["id"] for item in second["canvases"]], ["a-old"])
        self.assertFalse(second["has_more"])

        # Counts always describe every project, not the page. Unclaimed legacy
        # canvases are readable but not editable, so they never count as removable.
        self.assertEqual(first["counts"]["p1"], {"total": 3, "editable": 0})
        self.assertEqual(first["counts"]["p2"], {"total": 1, "editable": 0})

    def test_default_project_fallback_and_full_list_compat(self):
        self.write_canvas("no-project", updated_at=5)
        self.write_canvas("explicit", project="default", updated_at=6)
        self.write_canvas("other", project="p2", updated_at=7)

        scoped = self.run_page(project_id="default", limit=60)
        self.assertEqual({item["id"] for item in scoped["canvases"]}, {"no-project", "explicit"})

        everything = self.run_page()
        self.assertEqual(everything["limit"], 0)
        self.assertEqual(everything["total"], 3)
        self.assertFalse(everything["has_more"])
        self.assertEqual(len(everything["canvases"]), 3)

    def test_query_matches_titles_and_reports_match_total(self):
        self.write_canvas("map-a", title="地图 A", updated_at=1)
        self.write_canvas("map-b", title="地图 B", updated_at=2)
        self.write_canvas("poster", title="海报", updated_at=3)

        result = self.run_page(project_id="default", query="地图", limit=60)
        self.assertEqual({item["id"] for item in result["canvases"]}, {"map-a", "map-b"})
        self.assertEqual(result["total"], 2)

    def test_sharing_roles_drive_editable_counts(self):
        self.write_canvas(
            "mine",
            owner_user_id="user-1",
            editor_user_ids=[],
            ownership_state="claimed",
            sharing_version=1,
        )
        self.write_canvas(
            "shared",
            owner_user_id="user-2",
            editor_user_ids=["user-1"],
            ownership_state="claimed",
            sharing_version=1,
        )
        self.write_canvas(
            "readonly",
            owner_user_id="user-2",
            editor_user_ids=[],
            ownership_state="claimed",
            sharing_version=1,
        )
        # Legacy canvas without sharing_version stays readable but unclaimed.
        self.write_canvas("legacy")

        page = self.run_page(project_id="default", limit=60)
        self.assertEqual(page["counts"]["default"]["total"], 4)
        # owner + editor; the read-only and the unclaimed legacy canvas are not.
        self.assertEqual(page["counts"]["default"]["editable"], 2)
        roles = {item["id"]: item["sharing"]["role"] for item in page["canvases"]}
        self.assertEqual(roles["mine"], "owner")
        self.assertEqual(roles["shared"], "editor")
        self.assertEqual(roles["readonly"], "viewer")
        self.assertEqual(roles["legacy"], "viewer")
        legacy = next(item for item in page["canvases"] if item["id"] == "legacy")
        self.assertEqual(legacy["sharing"]["ownership_state"], "unclaimed")


class CanvasListPagingContractTests(unittest.TestCase):
    """The workspace board must ask for one project page, not the whole library."""

    @classmethod
    def setUpClass(cls):
        cls.list_js = (ROOT / "static" / "js" / "canvas-list.js").read_text(encoding="utf-8")
        cls.list_css = (ROOT / "static" / "css" / "canvas-list.css").read_text(encoding="utf-8")

    def test_board_requests_a_paged_project_url(self):
        self.assertIn("const CANVAS_PAGE_SIZE = 60;", self.list_js)
        self.assertIn("params.set('project_id', pid || 'default');", self.list_js)
        self.assertIn("params.set('limit', String(CANVAS_PAGE_SIZE));", self.list_js)
        self.assertIn("params.set('offset', String(Math.max(0, Number(offset) || 0)));", self.list_js)

    def test_stale_responses_and_empty_pages_are_handled(self):
        self.assertIn("if(requestId !== canvasPageRequestId) return;", self.list_js)
        self.assertIn(
            "canvasHasMore = Boolean(data.has_more) && incoming.length > 0;",
            self.list_js,
        )

    def test_load_more_tile_and_first_page_placeholder_exist(self):
        self.assertIn("function buildLoadMoreTile()", self.list_js)
        self.assertIn("grid.appendChild(buildLoadMoreTile())", self.list_js)
        self.assertIn(".ws-load-more-tile", self.list_css)
        self.assertIn(".ws-board-loading", self.list_css)
        self.assertIn(".ws-board-spinner", self.list_css)

    def test_cut_canvas_survives_a_project_switch(self):
        # Cards are paged per project, so the cut card is snapshotted before the
        # board reloads; a lookup in `canvases` would silently lose it.
        self.assertIn("clipboardCanvas = { ...c };", self.list_js)
        self.assertIn("await moveCanvasToProject(c.id, targetPid, c);", self.list_js)


if __name__ == "__main__":
    unittest.main()
