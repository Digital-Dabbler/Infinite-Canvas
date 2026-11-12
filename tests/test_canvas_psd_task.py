# -*- coding: utf-8 -*-
"""画布图片转 PSD 后端链路离线测试（全程 mock 网络，不产生任何费用）。"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from psd_layers import LAYER_DECOMPOSE_PATH

ROOT = Path(__file__).resolve().parents[1]

PROVIDER = {
    "id": "runninghub",
    "base_url": "https://www.runninghub.cn",
    "api_key": "plain-key-test",
    "wallet_api_key": "wallet-key-test",
}
CTX = SimpleNamespace(provider=PROVIDER, provider_id="runninghub", api_profile_id="legacy-shared")

SUCCESS_QUERY_RAW = {
    "code": 0,
    "msg": "success",
    "data": {
        "taskId": "2120440366181736450",
        "status": "SUCCESS",
        "results": [
            {"url": "https://rh.example/base.png", "zindex": 0, "size": "1199x1200",
             "background": "opaque", "name": "", "description": "整幅背景"},
            {"url": "https://rh.example/title.png", "zindex": 1, "name": "标题字",
             "size": "391x290", "background": "transparent",
             "boundingBox": {"absolute": [398, 0, 789, 290], "normalized": [331, 0, 656, 241]}},
            {"url": "https://rh.example/person.png", "zindex": 2, "name": "人物",
             "size": "440x600", "background": "transparent",
             "boundingBox": {"absolute": [120, 300, 560, 900], "normalized": [100, 250, 466, 750]}},
        ],
    },
}


def fake_request(user_id="u1"):
    return SimpleNamespace(state=SimpleNamespace(), cookies={}, headers={}, client=SimpleNamespace(host="test"))


def owner_user():
    return {"id": "u1", "role": "user"}


def prepared_info(w, h, resized=False, upload_url=None, source_url="/assets/output/src.png"):
    """构造 psd_prepare_upload_source 的返回值（mock 用）。"""
    return {
        "upload_url": upload_url or source_url,
        "width": w, "height": h,
        "source_url": source_url, "source_width": w, "source_height": h,
        "resized": resized,
    }


class CanvasPsdTaskBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data = root / "data"
        self.canvases = self.data / "canvases"
        self.canvases.mkdir(parents=True)
        self.assets = root / "assets"
        self.output = self.assets / "output"
        self.output.mkdir(parents=True)
        self.patches = [
            patch.object(main, "DATA_DIR", str(self.data)),
            patch.object(main, "CANVAS_DIR", str(self.canvases)),
            patch.object(main, "CANVAS_TASKS_FILE", str(self.data / "canvas_tasks.json")),
            patch.object(main, "ASSETS_DIR", str(self.assets)),
            patch.object(main, "OUTPUT_OUTPUT_DIR", str(self.output)),
            patch.object(main, "OUTPUT_INPUT_DIR", str(self.assets / "input")),
            patch.object(main.manager, "broadcast_canvas_operation", AsyncMock()),
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

    def make_canvas(self, canvas_id, node):
        canvas = {
            "id": canvas_id,
            "title": "测试画布",
            "kind": "smart",
            "nodes": [node],
            "connections": [],
            "logs": [],
            "media_catalog": [],
            "deleted_node_ids": [],
            "operation_log": [],
            "sync_revision": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        with open(self.canvases / f"{canvas_id}.json", "w", encoding="utf-8") as fh:
            json.dump(canvas, fh, ensure_ascii=False)
        return canvas

    def make_pending_node(self, canvas_id, node_id, task_id, log_run=None):
        node = {
            "id": node_id,
            "type": "smart-psd",
            "title": "图片转PSD",
            "images": [],
            "activeImageIndex": 0,
            "psd": None,
            "pendingTasks": [{
                "taskId": task_id,
                "kind": "image",
                "providerId": "runninghub",
                "model": "seedream-v5.5-layer-decomposition",
                "batchId": "batch-1",
                "serverManaged": True,
                "logRun": log_run or {"nodeType": "smart-psd"},
                "logStartedAt": int(time.time() * 1000),
            }],
            "pending": 1,
            "running": False,
        }
        self.make_canvas(canvas_id, node)
        main.canvas_task_create({
            "id": task_id,
            "type": "canvas-psd",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
            "provider_id": "runninghub",
            "api_profile_id": "legacy-shared",
            "model": "seedream-v5.5-layer-decomposition",
            "user_id": "u1",
            "usage_event_id": "ev-1",
            "canvas_binding": {"canvas_id": canvas_id, "node_id": node_id, "batch_id": "batch-1"},
        })
        return node

    def psd_payload(self, **overrides):
        data = {
            "canvas_id": "c-psd",
            "node_id": "psd_1",
            "batch_id": "batch-1",
            "source_url": "/assets/output/src.png",
            "prompt": "把标题和人物拆出来",
            "resolution": "1k",
            "max_images": 20,
        }
        data.update(overrides)
        return main.CanvasPsdRequest(**data)


class FakeHttp:
    """替 httpx.AsyncClient 记账：记录每次 post，返回可控 payload。"""

    def __init__(self, submit_payload):
        self.calls = []
        self._submit = submit_payload

    def factory(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "json": dict(json or {})})
        return self._Response(self._submit)

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload


class RunnerSuccessTest(CanvasPsdTaskBase):
    async def test_success_flow_wallet_headers_and_node_psd(self):
        canvas_id, node_id, task_id = "c-psd", "psd_1", "canvas_psd_ok"
        self.make_pending_node(canvas_id, node_id, task_id)
        http = FakeHttp({"code": 0, "data": {"taskId": "2120440366181736450", "status": "RUNNING"}})
        event = {"id": "ev-1", "user_id": "u1", "api_profile_id": "legacy-shared"}
        finish = Mock()
        upload = AsyncMock(return_value="https://rh.example/uploaded.png")
        with patch.object(main, "upstream_context_for_profile_id", Mock(return_value=CTX)), \
             patch.object(main, "psd_prepare_upload_source", Mock(return_value=prepared_info(1199, 1200))), \
             patch.object(main, "runninghub_upload_reference", upload), \
             patch.object(main, "wait_for_runninghub_openapi_task", AsyncMock(return_value=SUCCESS_QUERY_RAW)), \
             patch.object(main, "save_psd_layer_image", AsyncMock(side_effect=lambda url, index: f"/assets/output/psd_layer{index}_x.png")), \
             patch.object(main, "finish_usage_event", finish), \
             patch.object(main.httpx, "AsyncClient", http.factory):
            await main.run_canvas_psd_task(task_id, self.psd_payload(), event, "legacy-shared")

        # 1) 提交走 v2 拆分端点 + 企业共享钱包 Bearer（优先于普通 Key），body 无 apiKey。
        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertTrue(call["url"].endswith(LAYER_DECOMPOSE_PATH), call["url"])
        self.assertEqual(call["headers"].get("Authorization"), "Bearer wallet-key-test")
        self.assertEqual(call["json"]["imageUrl"], "https://rh.example/uploaded.png")
        self.assertEqual(call["json"]["resolution"], "1k")
        self.assertEqual(call["json"]["maxImages"], 20)
        self.assertNotIn("apiKey", call["json"])
        # 上传必须走 runninghub.ai 域（共享钱包 Key 在 .cn 上传不被认），提交/轮询保持 .cn。
        upload_provider = upload.await_args.args[1]
        self.assertEqual(upload_provider["base_url"], main.RUNNINGHUB_LAYER_UPLOAD_BASE_URL)
        self.assertTrue(upload_provider["base_url"].endswith("runninghub.ai"))
        self.assertTrue(call["url"].startswith("https://www.runninghub.cn/openapi/v2/"), call["url"])

        # 2) 任务收口 succeeded，用量事件同 id 记 succeeded。
        task = main.canvas_task_get(task_id)
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["result"]["layerCount"], 3)
        self.assertEqual(task["result"]["upstream_task_id"], "2120440366181736450")
        finish.assert_called_once()
        self.assertEqual(finish.call_args[0][1], "succeeded")

        # 3) node.psd 落库：底图在 0 位、坐标换算、逐层本地 URL；images 与窄操作日志齐全。
        canvas = json.loads((self.canvases / f"{canvas_id}.json").read_text(encoding="utf-8"))
        node = canvas["nodes"][0]
        psd = node["psd"]
        self.assertEqual(psd["size"], {"width": 1199, "height": 1200})
        self.assertEqual(len(psd["layers"]), 3)
        self.assertTrue(psd["layers"][0]["isBase"])
        self.assertEqual(psd["layers"][0]["url"], "/assets/output/psd_layer0_x.png")
        self.assertEqual(psd["layers"][1]["x"], 398)
        self.assertEqual(psd["layers"][2]["height"], 600)
        self.assertEqual(node["title"], "图层拆分")
        self.assertEqual(len(node["images"]), 3)
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(node["psd"]["taskId"], task_id)
        self.assertTrue(any(log.get("local_task_id") == task_id for log in canvas["logs"]))
        main.manager.broadcast_canvas_operation.assert_awaited()

    async def test_small_image_auto_upscaled_before_upload(self):
        # 实测：500×333 小图上游报 1013 拒绝。现在预处理自动等比放大到最短边 512 再上传。
        # 本用例验证 runner 把「预处理后的图」交给上传，而不是原图。
        canvas_id, node_id, task_id = "c-psd", "psd_1", "canvas_psd_small"
        self.make_pending_node(canvas_id, node_id, task_id)
        http = FakeHttp({"code": 0, "data": {"taskId": "2120440366181736450", "status": "RUNNING"}})
        event = {"id": "ev-1", "user_id": "u1", "api_profile_id": "legacy-shared"}
        finish = Mock()
        upload = AsyncMock(return_value="https://rh.example/uploaded.png")
        prepared = prepared_info(768, 512, resized=True, upload_url="/assets/output/psd_prepared_test.png")
        with patch.object(main, "upstream_context_for_profile_id", Mock(return_value=CTX)), \
             patch.object(main, "psd_prepare_upload_source", Mock(return_value=prepared)) as prep, \
             patch.object(main, "runninghub_upload_reference", upload), \
             patch.object(main, "wait_for_runninghub_openapi_task", AsyncMock(return_value=SUCCESS_QUERY_RAW)), \
             patch.object(main, "save_psd_layer_image", AsyncMock(side_effect=lambda url, index: f"/assets/output/psd_layer{index}_x.png")), \
             patch.object(main, "finish_usage_event", finish), \
             patch.object(main.httpx, "AsyncClient", http.factory):
            await main.run_canvas_psd_task(task_id, self.psd_payload(), event, "legacy-shared")
        prep.assert_called_once()
        # 上传的是预处理放大图，不是原图。
        self.assertEqual(upload.await_args.args[2]["url"], "/assets/output/psd_prepared_test.png")
        task = main.canvas_task_get(task_id)
        self.assertEqual(task["status"], "succeeded")
        canvas = json.loads((self.canvases / f"{canvas_id}.json").read_text(encoding="utf-8"))
        psd = canvas["nodes"][0]["psd"]
        # source 保留用户原图信息；preprocessed.resized 标记放大过。
        self.assertEqual(psd["source"]["width"], 768)
        self.assertTrue(psd["preprocessed"]["resized"])

    def test_prepare_upload_source_real_resize(self):
        # 真函数：500×333 → 最短边拉到 512（768×512），落盘站内 PNG；≥512 原样直传。
        from PIL import Image
        small = self.output / "tiny_src.png"
        Image.new("RGB", (500, 333), (10, 200, 30)).save(small)
        info = main.psd_prepare_upload_source("/assets/output/tiny_src.png")
        self.assertTrue(info["resized"])
        # 500×333 → 最短边拉到 512：512/333≈1.5375，500×1.5375=768.77→round=769。
        self.assertEqual((info["width"], info["height"]), (769, 512))
        self.assertEqual(info["source_width"], 500)
        self.assertEqual(info["source_height"], 333)
        prepared_path = main.output_file_from_url(info["upload_url"])
        self.assertIsNotNone(prepared_path)
        with Image.open(prepared_path) as im:
            self.assertEqual(im.size, (769, 512))
        # 大图不动：upload_url=原图、resized=False。
        big = self.output / "big_src.png"
        Image.new("RGB", (1200, 900), (5, 5, 5)).save(big)
        info2 = main.psd_prepare_upload_source("/assets/output/big_src.png")
        self.assertFalse(info2["resized"])
        self.assertEqual(info2["upload_url"], "/assets/output/big_src.png")

    async def test_poll_timeout_marks_recoverable_without_resubmit(self):
        canvas_id, node_id, task_id = "c-psd", "psd_1", "canvas_psd_to"
        self.make_pending_node(canvas_id, node_id, task_id)
        http = FakeHttp({"code": 0, "data": {"taskId": "2120440366181736450"}})
        timeout_error = main.HTTPException(status_code=504, detail="RunningHub 仍在处理中，可稍后查询结果（任务 ID：2120440366181736450）")
        timeout_error.recovery_available = True
        event = {"id": "ev-1", "user_id": "u1", "api_profile_id": "legacy-shared"}
        finish = Mock()
        scope = Mock()
        wait = AsyncMock(side_effect=timeout_error)
        with patch.object(main, "upstream_context_for_profile_id", Mock(return_value=CTX)), \
             patch.object(main, "psd_prepare_upload_source", Mock(return_value=prepared_info(1199, 1200))), \
             patch.object(main, "runninghub_upload_reference", AsyncMock(return_value="https://rh.example/uploaded.png")), \
             patch.object(main, "wait_for_runninghub_openapi_task", wait), \
             patch.object(main, "finish_usage_event", finish), \
             patch.object(main, "save_upstream_task_scope", scope), \
             patch.object(main.httpx, "AsyncClient", http.factory):
            await main.run_canvas_psd_task(task_id, self.psd_payload(), event, "legacy-shared")

        task = main.canvas_task_get(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task["recovery_available"])
        self.assertEqual(task["upstream_task_id"], "2120440366181736450")
        finish.assert_called_once()
        self.assertEqual(finish.call_args[0][1], "failed")
        # 续查 scope 必须记回钱包凭据类型，恢复查询才不会走错 Key。
        scope.assert_called_once()
        self.assertEqual(scope.call_args.kwargs.get("credential_kind"), "runninghub_wallet")
        # 提交只发生一次：超时绝不自动重提上游。
        self.assertEqual(len(http.calls), 1)
        canvas = json.loads((self.canvases / f"{canvas_id}.json").read_text(encoding="utf-8"))
        node = canvas["nodes"][0]
        self.assertNotIn("pendingTasks", node)
        self.assertIn("RunningHub", node.get("lastRunError", ""))


class ExportTest(CanvasPsdTaskBase):
    def _write_layer_pngs(self, names_sizes):
        from PIL import Image
        urls = {}
        for name, (w, h, color) in names_sizes.items():
            rel = f"layer_{name}.png"
            image = Image.new("RGBA", (w, h), color)
            image.save(self.output / rel)
            urls[name] = f"/assets/output/{rel}"
        return urls

    def test_export_builds_psd_and_stores_exported_file(self):
        canvas_id, node_id = "c-psd", "psd_1"
        urls = self._write_layer_pngs({
            "base": (6, 5, (255, 0, 0, 255)),
            "top": (2, 2, (0, 0, 255, 255)),
        })
        node = {
            "id": node_id, "type": "smart-psd", "title": "图片转PSD", "images": [],
            "psd": {
                "source": {"url": "/assets/output/src.png", "width": 6, "height": 5},
                "size": {"width": 6, "height": 5},
                "layers": [
                    {"id": "base_0_x", "name": "背景", "url": urls["base"], "x": 0, "y": 0,
                     "width": 6, "height": 5, "visible": True, "isBase": True},
                    {"id": "layer_1_x", "name": "上层物体", "url": urls["top"], "x": 3, "y": 2,
                     "width": 2, "height": 2, "visible": False, "isBase": False},
                ],
                "exportedFile": None, "taskId": "canvas_psd_x",
            },
        }
        self.make_canvas(canvas_id, node)
        canvas, updated, revision, exported = main.build_canvas_psd_file(canvas_id, node_id)
        self.assertTrue(exported["url"].endswith(".psd"))
        path = main.output_file_from_url(exported["url"])
        self.assertIsNotNone(path)
        data = Path(path).read_bytes()
        self.assertEqual(data[:4], b"8BPS")
        self.assertEqual(int.from_bytes(data[4:6], "big"), 1)  # PSD 版本 1（签名后紧接 2 字节）
        self.assertEqual(int.from_bytes(data[12:14], "big"), 4)  # RGBA 通道数
        self.assertEqual(int.from_bytes(data[14:18], "big"), 5)  # 高
        self.assertEqual(int.from_bytes(data[18:22], "big"), 6)  # 宽
        self.assertEqual(exported["sizeBytes"], len(data))
        self.assertEqual(exported["layerCount"], 2)
        self.assertGreaterEqual(revision, 1)
        reloaded = json.loads((self.canvases / f"{canvas_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(reloaded["nodes"][0]["psd"]["exportedFile"]["name"], exported["name"])

    def test_export_requires_layers(self):
        canvas_id, node_id = "c-psd", "psd_1"
        self.make_canvas(canvas_id, {"id": node_id, "type": "smart-psd", "psd": None})
        with self.assertRaises(main.HTTPException):
            main.build_canvas_psd_file(canvas_id, node_id)


class RoutesTest(CanvasPsdTaskBase):
    async def test_post_rejects_bad_params_before_usage_event(self):
        with patch.object(main, "require_canvas_task_editor", Mock(return_value={"canvas_id": "c", "node_id": "n", "batch_id": "b"})), \
             patch.object(main, "begin_usage_event") as begin:
            with self.assertRaises(main.HTTPException) as ctx:
                await main.create_canvas_psd_task(self.psd_payload(resolution="3k"), fake_request())
            self.assertEqual(ctx.exception.status_code, 400)
            with self.assertRaises(main.HTTPException):
                await main.create_canvas_psd_task(self.psd_payload(prompt="x" * 2001), fake_request())
            with self.assertRaises(main.HTTPException):
                await main.create_canvas_psd_task(self.psd_payload(max_images=200), fake_request())
            with self.assertRaises(main.HTTPException):
                await main.create_canvas_psd_task(self.psd_payload(source_url="  "), fake_request())
            begin.assert_not_called()

    async def test_post_creates_task_and_runner(self):
        canvas_id, node_id = "c-psd", "psd_1"
        node = {"id": node_id, "type": "smart-psd", "images": [], "pendingTasks": [], "pending": 0}
        self.make_canvas(canvas_id, node)
        event = {"id": "ev-9", "user_id": "u1", "api_profile_id": "legacy-shared"}
        runner = AsyncMock(return_value=None)
        remember = Mock()
        with patch.object(main, "require_canvas_task_editor", Mock(return_value={"canvas_id": canvas_id, "node_id": node_id, "batch_id": "batch-1"})), \
             patch.object(main, "begin_usage_event", Mock(return_value=event)), \
             patch.object(main, "upstream_context_for_profile_id", Mock(return_value=CTX)), \
             patch.object(main, "remember_upstream_task_scope", remember), \
             patch.object(main, "run_canvas_psd_task", runner):
            response = await main.create_canvas_psd_task(self.psd_payload(), fake_request())
            await asyncio.sleep(0.02)
        self.assertTrue(response["ok"])
        self.assertTrue(response["task_id"].startswith("canvas_psd_"))
        self.assertEqual(response["canvas_bound"], True)
        self.assertEqual(main.canvas_task_get(response["task_id"])["type"], "canvas-psd")
        remember.assert_called_once()
        self.assertEqual(remember.call_args.kwargs.get("credential_kind"), "runninghub_wallet")
        runner.assert_awaited_once()
        canvas = json.loads((self.canvases / f"{canvas_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(canvas["nodes"][0]["pendingTasks"][0]["taskId"], response["task_id"])

    async def test_get_is_owner_scoped_and_type_scoped(self):
        task_id = "canvas_psd_g"
        main.canvas_task_create({
            "id": task_id, "type": "canvas-psd", "status": "succeeded", "user_id": "u1",
            "created_at": time.time(), "updated_at": time.time(),
        })
        main.canvas_task_create({
            "id": "canvas_img_g", "type": "online-image", "status": "succeeded", "user_id": "u1",
            "created_at": time.time(), "updated_at": time.time(),
        })
        with patch.object(main, "require_authenticated", Mock(return_value=owner_user())):
            task = await main.get_canvas_psd_task(task_id, fake_request())
            self.assertEqual(task["id"], task_id)
        with patch.object(main, "require_authenticated", Mock(return_value={"id": "u2", "role": "user"})):
            with self.assertRaises(main.HTTPException) as forbid:
                await main.get_canvas_psd_task(task_id, fake_request())
            self.assertEqual(forbid.exception.status_code, 403)
        with patch.object(main, "require_authenticated", Mock(return_value=owner_user())):
            with self.assertRaises(main.HTTPException) as wrong_type:
                await main.get_canvas_psd_task("canvas_img_g", fake_request())
            self.assertEqual(wrong_type.exception.status_code, 404)


class ModelTest(unittest.TestCase):
    def test_defaults(self):
        payload = main.CanvasPsdRequest()
        self.assertEqual(payload.resolution, "auto")
        self.assertEqual(payload.max_images, main.psd_layers.LAYER_DEFAULT_MAX_IMAGES)
        self.assertEqual(payload.source_url, "")
        export = main.CanvasPsdExportRequest(canvas_id="c", node_id="n")
        self.assertEqual(export.node_id, "n")


if __name__ == "__main__":
    unittest.main()
