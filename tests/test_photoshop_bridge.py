import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class PhotoshopBridgeTests(unittest.TestCase):
    def test_build_package_excludes_local_backup_helper(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
            / "build-package.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn('$_.Name -ne "backup-latest-plugin.bat"', script)

    def test_latest_installer_uses_newest_zip_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "bridge-0.1.zip"
            newest = root / "bridge-0.2.zip"
            ignored = root / "notes.txt"
            older.write_bytes(b"old")
            newest.write_bytes(b"new")
            ignored.write_text("ignore", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newest, (200, 200))

            installer = main.latest_photoshop_bridge_installer(directory)

        self.assertEqual(installer["name"], newest.name)
        self.assertEqual(installer["path"], str(newest))
        self.assertEqual(installer["size"], 3)

    def test_connection_manager_checks_user_scoped_plugin(self):
        manager = main.ConnectionManager()
        websocket = object()
        manager.active_connections.append(websocket)
        manager.user_connections["user-1:photoshop-canvas-bridge"] = websocket

        self.assertTrue(manager.has_user_client("user-1", "photoshop-canvas-bridge"))
        self.assertFalse(manager.has_user_client("user-2", "photoshop-canvas-bridge"))

    def test_connection_manager_finds_multiple_photoshop_instances(self):
        manager = main.ConnectionManager()
        first = object()
        second = object()
        manager.active_connections.extend([first, second])
        manager.user_connections["user-1:photoshop-canvas-bridge:pc-a"] = first
        manager.user_connections["user-1:photoshop-canvas-bridge:pc-b"] = second

        matches = manager.user_client_connections("user-1", "photoshop-canvas-bridge")

        self.assertEqual({item[1] for item in matches}, {first, second})
        self.assertTrue(manager.has_user_client("user-1", "photoshop-canvas-bridge"))

    def test_cep_manifest_uses_cc2018_compatible_schema(self):
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
            / "CSXS"
            / "manifest.xml"
        )
        root = ElementTree.parse(manifest_path).getroot()
        runtime = root.find("./ExecutionEnvironment/RequiredRuntimeList/RequiredRuntime")

        self.assertEqual(root.attrib["Version"], "7.0")
        self.assertEqual(root.attrib["ExtensionBundleVersion"], "0.2.7")
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.attrib["Name"], "CSXS")
        self.assertEqual(runtime.attrib["Version"], "7.0")

    def test_panel_uses_canvas_entry_icons_for_light_and_dark_themes(self):
        bridge_root = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
        )
        manifest = ElementTree.parse(bridge_root / "CSXS" / "manifest.xml").getroot()
        icons = {
            icon.attrib["Type"]: icon.text
            for icon in manifest.findall("./DispatchInfoList/Extension/DispatchInfo/UI/Icons/Icon")
        }
        self.assertEqual(
            icons,
            {
                "Normal": "./icons/canvas-light.png",
                "RollOver": "./icons/canvas-light.png",
                "DarkNormal": "./icons/canvas-dark.png",
                "DarkRollOver": "./icons/canvas-dark.png",
            },
        )

        for theme in ("light", "dark"):
            for suffix, expected_size in (("", (23, 23)), ("_x2", (46, 46))):
                icon_path = bridge_root / "icons" / f"canvas-{theme}{suffix}.png"
                png = icon_path.read_bytes()
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", png[16:24]), expected_size)

    def test_panel_css_avoids_unsupported_cc2018_positioning(self):
        css_path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
            / "client"
            / "style.css"
        )
        css = css_path.read_text(encoding="utf-8").replace(" ", "")

        self.assertNotIn("inset:", css)
        self.assertNotIn("width:min(", css)
        self.assertIn("top:0;right:0;bottom:0;left:0", css)

    def test_panel_snapshots_history_before_realtime_auto_open(self):
        app_path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
            / "client"
            / "js"
            / "app.js"
        )
        source = app_path.read_text(encoding="utf-8")

        self.assertIn("socketHistoryIds[task.id] = true", source)
        self.assertIn("if (isHistorical) { return; }", source)

    def test_panel_recovers_inbox_on_reconnect_without_constant_polling(self):
        app_path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
            / "client"
            / "js"
            / "app.js"
        )
        source = app_path.read_text(encoding="utf-8")

        self.assertIn('currentSocket.send("ping")', source)
        self.assertIn("Date.now() - lastSocketActivity > 45000", source)
        self.assertIn("refreshTasks(true)", source)
        self.assertNotIn("inboxPollTimer", source)
        self.assertNotIn("}, 3000);", source)
        self.assertIn("claimAndOpen(task, true)", source)
        self.assertIn("if (inboxBaselineReady && task && task.id", source)
        self.assertIn('"photoshop-canvas-bridge:" + instanceId', source)

    def test_panel_registers_photoshop_persistent_before_connecting(self):
        root = Path(__file__).resolve().parents[1] / "tools" / "photoshop-canvas-bridge" / "client" / "js"
        app_source = (root / "app.js").read_text(encoding="utf-8")
        cep_source = (root / "cep.js").read_text(encoding="utf-8")

        initial_registration = app_source.rfind("cep.requestPersistent();")
        automatic_connect = app_source.rfind("if (net.state.token) { connect(); }")
        self.assertGreater(initial_registration, 0)
        self.assertLess(initial_registration, automatic_connect)
        self.assertIn('scope:"APPLICATION"', cep_source)
        self.assertIn('extensionId:extensionId || "com.daxiong.infinitecanvas.bridge.panel"', cep_source)
        self.assertNotIn("appId:appId", cep_source)

    def test_panel_can_switch_users_without_reusing_old_token(self):
        app_path = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "photoshop-canvas-bridge"
            / "client"
            / "js"
            / "app.js"
        )
        source = app_path.read_text(encoding="utf-8")

        self.assertIn('el.connect.textContent = value ? "切换账号" : "连接画布"', source)
        self.assertIn('localStorage.removeItem(LS_TOKEN)', source)
        self.assertIn('var wantsLogin = Boolean(username || password)', source)
        self.assertIn('wantsLogin ? net.login(username, password)', source)
        self.assertIn('收件箱只显示此账号的互传记录', source)

    def test_inbox_has_separate_clear_and_refresh_controls(self):
        root = Path(__file__).resolve().parents[1] / "tools" / "photoshop-canvas-bridge" / "client"
        markup = (root / "index.html").read_text(encoding="utf-8")
        source = (root / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="clearButton"', markup)
        self.assertIn(">一键清除</button>", markup)
        self.assertIn('aria-label="刷新收件箱"', markup)
        self.assertIn('el.clear.addEventListener("click"', source)
        self.assertIn("clearTaskInbox(false);", source)
        self.assertIn("hiddenTaskIds[task.id] = true", source)
        self.assertIn("!hiddenTaskIds[task.id]", source)
        self.assertIn("hiddenTaskIds = {};", source)

    def test_canvas_picker_groups_canvases_by_project(self):
        root = Path(__file__).resolve().parents[1] / "tools" / "photoshop-canvas-bridge" / "client"
        source = (root / "js" / "app.js").read_text(encoding="utf-8")
        styles = (root / "style.css").read_text(encoding="utf-8")

        self.assertIn('groups[projectId].canvases.push(canvas)', source)
        self.assertIn('data-project-toggle=', source)
        self.assertIn('collapsedProjects[projectId] = willCollapse', source)
        self.assertIn('.canvas-project.collapsed .canvas-project-items', styles)

    def test_generation_return_appends_and_activates_result(self):
        canvas = {
            "id": "canvas-1",
            "kind": "smart",
            "nodes": [{
                "id": "gen-1",
                "type": "smart-image-generation",
                "images": [{"url": "/assets/input/original.png"}],
                "activeImageIndex": 0,
            }],
        }
        task = {"node_id": "gen-1"}
        returned = {"url": "/assets/input/returned.png", "name": "returned.png", "kind": "image"}

        with patch.object(main, "save_canvas"):
            node = main.append_photoshop_return_to_canvas(canvas, task, returned)

        self.assertEqual(node["id"], "gen-1")
        self.assertEqual(node["images"][-1]["url"], returned["url"])
        self.assertEqual(node["activeImageIndex"], 1)

    def test_upload_return_creates_adjacent_upload_node(self):
        canvas = {
            "id": "canvas-1",
            "kind": "smart",
            "nodes": [{
                "id": "upload-1",
                "type": "smart-image-upload",
                "x": 100,
                "y": 80,
                "w": 300,
                "images": [{"url": "/assets/input/original.png"}],
            }],
        }
        task = {"node_id": "upload-1"}
        returned = {"url": "/assets/input/returned.png", "name": "returned.png", "kind": "image"}

        with patch.object(main, "save_canvas"):
            node = main.append_photoshop_return_to_canvas(canvas, task, returned)

        self.assertEqual(node["type"], "smart-image-upload")
        self.assertEqual(node["images"], [returned])
        self.assertGreater(node["x"], canvas["nodes"][0]["x"])
        self.assertEqual(len(canvas["nodes"]), 2)

    def test_source_rejects_classic_canvas(self):
        with self.assertRaises(main.HTTPException) as raised:
            main.photoshop_bridge_source({"kind": "classic", "nodes": []}, "node-1", 0)
        self.assertEqual(raised.exception.status_code, 400)

    def test_return_item_reads_image_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "returned.png"
            main.Image.new("RGB", (123, 77), "white").save(path)
            with patch.object(main, "output_file_from_url", return_value=str(path)):
                item = main.photoshop_bridge_return_item("/assets/input/returned.png", "edited.png")

        self.assertEqual(item["natural_w"], 123)
        self.assertEqual(item["natural_h"], 77)
        self.assertEqual(item["kind"], "image")

    def test_free_import_creates_new_nodes_at_the_right(self):
        canvas = {
            "id": "canvas-1",
            "kind": "smart",
            "nodes": [{
                "id": "existing",
                "type": "smart-image-upload",
                "x": 100,
                "y": 40,
                "w": 300,
            }],
        }
        item = {"url": "/assets/input/edited.png", "name": "edited.png", "kind": "image"}

        with patch.object(main, "save_canvas"):
            first = main.create_photoshop_upload_node(
                canvas, item, "selection", {"left": 3, "top": 4, "right": 103, "bottom": 84}
            )
            second = main.create_photoshop_upload_node(canvas, item, "document", {})

        self.assertEqual(first["type"], "smart-image-upload")
        self.assertEqual(first["images"], [item])
        self.assertEqual(first["photoshopImport"]["scope"], "selection")
        self.assertGreater(first["x"], 400)
        self.assertGreater(second["x"], first["x"])
        self.assertEqual(len(canvas["nodes"]), 3)

    def test_free_import_rejects_classic_canvas(self):
        with self.assertRaises(main.HTTPException) as raised:
            main.create_photoshop_upload_node(
                {"id": "classic", "kind": "classic", "nodes": []},
                {"url": "/assets/input/edited.png", "name": "edited.png", "kind": "image"},
            )
        self.assertEqual(raised.exception.status_code, 400)


class PhotoshopBridgeStateTests(unittest.IsolatedAsyncioTestCase):
    def request(self, user_id="user-1"):
        return SimpleNamespace(state=SimpleNamespace(user={"id": user_id}))

    async def test_claim_is_idempotent_and_exclusive_across_clients(self):
        tasks = [{
            "id": "task-1",
            "user_id": "user-1",
            "status": "pending",
            "created_at": 1_000_000,
            "updated_at": 1_000_000,
        }]
        with (
            patch.object(main, "load_photoshop_bridge_tasks", return_value=tasks),
            patch.object(main, "save_photoshop_bridge_tasks"),
            patch.object(main, "now_ms", return_value=1_100_000),
        ):
            first = await main.claim_photoshop_bridge_task(
                "task-1", main.PhotoshopBridgeClaimRequest(client_instance_id="client-a"), self.request()
            )
            duplicate = await main.claim_photoshop_bridge_task(
                "task-1", main.PhotoshopBridgeClaimRequest(client_instance_id="client-a"), self.request()
            )
            other = await main.claim_photoshop_bridge_task(
                "task-1", main.PhotoshopBridgeClaimRequest(client_instance_id="client-b"), self.request()
            )
            opened = await main.mark_photoshop_bridge_task_opened(
                "task-1", main.PhotoshopBridgeOpenResultRequest(client_instance_id="client-a"), self.request()
            )

        self.assertTrue(first["should_open"])
        self.assertFalse(duplicate["should_open"])
        self.assertFalse(other["should_open"])
        self.assertEqual(opened["task"]["status"], "opened")
        self.assertEqual(opened["task"]["claimed_by"], "client-a")

    async def test_expired_claim_can_be_recovered_by_another_client(self):
        now = 2_000_000
        tasks = [{
            "id": "task-1",
            "user_id": "user-1",
            "status": "opening",
            "claimed_by": "client-a",
            "claimed_at": now - main.PHOTOSHOP_BRIDGE_CLAIM_LEASE_MS - 1,
            "created_at": now - 1000,
            "updated_at": now - 1000,
        }]
        with (
            patch.object(main, "load_photoshop_bridge_tasks", return_value=tasks),
            patch.object(main, "save_photoshop_bridge_tasks"),
            patch.object(main, "now_ms", return_value=now),
        ):
            result = await main.claim_photoshop_bridge_task(
                "task-1", main.PhotoshopBridgeClaimRequest(client_instance_id="client-b"), self.request()
            )

        self.assertTrue(result["should_open"])
        self.assertEqual(result["task"]["claimed_by"], "client-b")

    async def test_open_failure_is_visible_and_retryable(self):
        tasks = [{
            "id": "task-1",
            "user_id": "user-1",
            "status": "pending",
            "created_at": 1_000_000,
            "updated_at": 1_000_000,
        }]
        with (
            patch.object(main, "load_photoshop_bridge_tasks", return_value=tasks),
            patch.object(main, "save_photoshop_bridge_tasks"),
            patch.object(main, "now_ms", return_value=1_100_000),
        ):
            await main.claim_photoshop_bridge_task(
                "task-1", main.PhotoshopBridgeClaimRequest(client_instance_id="client-a"), self.request()
            )
            failed = await main.mark_photoshop_bridge_task_failed(
                "task-1",
                main.PhotoshopBridgeOpenResultRequest(client_instance_id="client-a", error="文件损坏"),
                self.request(),
            )
            retried = await main.claim_photoshop_bridge_task(
                "task-1", main.PhotoshopBridgeClaimRequest(client_instance_id="client-a"), self.request()
            )

        self.assertEqual(failed["task"]["status"], "open_failed")
        self.assertEqual(failed["task"]["error"], "文件损坏")
        self.assertTrue(retried["should_open"])
        self.assertEqual(retried["task"]["status"], "opening")

    async def test_task_list_filters_user_age_and_count(self):
        now = 10_000_000_000
        recent = [
            {
                "id": "mine-%02d" % index,
                "user_id": "user-1",
                "status": "opened",
                "created_at": now - index,
                "updated_at": now - index,
            }
            for index in range(60)
        ]
        old = {
            "id": "old",
            "user_id": "user-1",
            "status": "opened",
            "created_at": now - main.PHOTOSHOP_BRIDGE_TASK_TTL_MS - 1,
            "updated_at": now - main.PHOTOSHOP_BRIDGE_TASK_TTL_MS - 1,
        }
        other = {
            "id": "other-user",
            "user_id": "user-2",
            "status": "opened",
            "created_at": now,
            "updated_at": now,
        }
        with (
            patch.object(main, "load_photoshop_bridge_tasks", return_value=recent + [old, other]),
            patch.object(main, "save_photoshop_bridge_tasks"),
            patch.object(main, "now_ms", return_value=now),
        ):
            result = await main.list_photoshop_bridge_tasks(self.request(), limit=50)

        self.assertEqual(len(result["tasks"]), 50)
        self.assertEqual(result["tasks"][0]["id"], "mine-00")
        self.assertNotIn("old", {item["id"] for item in result["tasks"]})
        self.assertNotIn("other-user", {item["id"] for item in result["tasks"]})

    async def test_status_reports_current_users_plugin_connection(self):
        with patch.object(main.manager, "has_user_client", return_value=True) as probe:
            result = await main.photoshop_bridge_status(self.request())

        self.assertTrue(result["online"])
        self.assertEqual(result["installer_download_url"], "/api/photoshop-bridge/installer/latest")
        probe.assert_called_once_with("user-1", main.PHOTOSHOP_BRIDGE_CLIENT_ID)

    async def test_installer_download_returns_fixed_latest_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "bridge-latest.zip"
            package.write_bytes(b"zip")
            with patch.object(
                main,
                "latest_photoshop_bridge_installer",
                return_value={"path": str(package), "name": package.name, "size": 3},
            ):
                response = await main.download_latest_photoshop_bridge_installer(self.request())

        self.assertEqual(response.path, str(package))
        self.assertEqual(response.media_type, "application/zip")
        self.assertIn("bridge-latest.zip", response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
