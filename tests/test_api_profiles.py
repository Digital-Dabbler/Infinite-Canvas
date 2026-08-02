import asyncio
import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class ApiProfileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.profiles_path = os.path.join(self.data_dir, "api_profiles.json")
        self.users_path = os.path.join(self.data_dir, "auth_users.json")
        self.env_path = os.path.join(self.data_dir, ".env")
        self.runninghub_workflow_store_path = os.path.join(
            self.data_dir, "runninghub_workflows.json"
        )
        self.providers = [
            {
                "id": "test-openai",
                "name": "测试平台",
                "base_url": "https://example.invalid/v1",
                "protocol": "openai",
                "enabled": True,
                "primary": True,
                "billing_scope": "department",
                "image_models": ["image-test"],
                "chat_models": ["chat-test"],
                "video_models": ["video-test"],
                "api_key": "TEST_SECRET_MUST_NOT_BE_SAVED",
            },
            {
                "id": "runninghub",
                "name": "RunningHub",
                "base_url": "https://www.runninghub.cn",
                "protocol": "runninghub",
                "enabled": True,
                "primary": False,
                "rh_apps": [{"id": "app-1", "name": "测试应用"}],
                "rh_workflows": [{"id": "workflow-1", "name": "测试工作流"}],
            },
        ]
        with open(self.users_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "users": [
                        {"id": "existing", "username": "existing", "department": "UI组"},
                        {
                            "id": "assigned",
                            "username": "assigned",
                            "department": "原画组",
                            "api_profile_id": "already-assigned",
                        },
                    ]
                },
                f,
                ensure_ascii=False,
            )
        self.patchers = [
            patch.object(main, "DATA_DIR", self.data_dir),
            patch.object(main, "API_PROFILES_FILE", self.profiles_path),
            patch.object(main, "AUTH_USERS_FILE", self.users_path),
            patch.object(main, "API_ENV_FILE", self.env_path),
            patch.object(
                main,
                "RUNNINGHUB_WORKFLOW_STORE_FILE",
                self.runninghub_workflow_store_path,
            ),
            patch.object(main, "load_api_providers", return_value=self.providers),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_first_migration_creates_legacy_profile_and_binds_existing_users(self):
        data = main.ensure_legacy_api_profile_migration()

        self.assertEqual(data["version"], 1)
        self.assertEqual([item["id"] for item in data["profiles"]], ["legacy-shared"])
        self.assertTrue(data["migration"]["legacy_users_bound"])
        legacy = data["profiles"][0]
        self.assertEqual([item["id"] for item in legacy["providers"]], ["test-openai", "runninghub"])
        self.assertEqual(legacy["providers"][0]["image_models"][0], "image-test")
        self.assertEqual(legacy["providers"][1]["rh_apps"][0]["id"], "app-1")

        with open(self.users_path, "r", encoding="utf-8") as f:
            users = json.load(f)["users"]
        self.assertEqual(users[0]["api_profile_id"], "legacy-shared")
        self.assertEqual(users[1]["api_profile_id"], "already-assigned")

        with open(self.profiles_path, "r", encoding="utf-8") as f:
            saved_text = f.read()
        self.assertNotIn("TEST_SECRET_MUST_NOT_BE_SAVED", saved_text)
        self.assertNotIn("api_key", saved_text)

    def test_migration_is_idempotent_and_does_not_assign_later_registrations(self):
        main.ensure_legacy_api_profile_migration()
        with open(self.users_path, "r", encoding="utf-8") as f:
            users_data = json.load(f)
        users_data["users"].append(
            {"id": "new-user", "username": "new-user", "department": "UI组", "api_profile_id": ""}
        )
        with open(self.users_path, "w", encoding="utf-8") as f:
            json.dump(users_data, f, ensure_ascii=False)

        first = main.ensure_legacy_api_profile_migration()
        second = main.ensure_legacy_api_profile_migration()

        self.assertEqual(first, second)
        self.assertEqual(len(second["profiles"]), 1)
        with open(self.users_path, "r", encoding="utf-8") as f:
            users = json.load(f)["users"]
        new_user = next(item for item in users if item["id"] == "new-user")
        self.assertEqual(new_user["api_profile_id"], "")

    def test_damaged_or_unknown_profile_file_uses_read_only_legacy_fallback(self):
        with open(self.profiles_path, "w", encoding="utf-8") as f:
            f.write("{damaged")
        damaged = main.load_api_profiles()
        self.assertEqual(damaged["profiles"][0]["id"], "legacy-shared")
        self.assertTrue(damaged["_fallback_reason"])

        with open(self.profiles_path, "w", encoding="utf-8") as f:
            json.dump({"version": 999, "profiles": []}, f)
        unknown = main.load_api_profiles()
        self.assertEqual(unknown["profiles"][0]["id"], "legacy-shared")
        self.assertIn("999", unknown["_fallback_reason"])

    def test_public_user_exposes_only_profile_assignment(self):
        public = main.public_user(
            {
                "id": "u1",
                "username": "user",
                "name": "用户",
                "department": "UI组",
                "api_profile_id": "ui-budget",
                "role": "user",
                "enabled": True,
            }
        )
        self.assertEqual(public["api_profile_id"], "ui-budget")
        self.assertNotIn("password_hash", public)

    def test_profile_credentials_use_composite_environment_names(self):
        self.assertEqual(main.provider_key_env("comfly", "legacy-shared"), "COMFLY_API_KEY")
        self.assertEqual(
            main.provider_key_env("test-openai", "ui-budget"),
            "API_PROFILE_UI_BUDGET_PROVIDER_TEST_OPENAI_KEY",
        )
        self.assertEqual(
            main.runninghub_wallet_key_env("ui-budget"),
            "API_PROFILE_UI_BUDGET_RUNNINGHUB_WALLET_API_KEY",
        )
        self.assertEqual(
            main.volcengine_access_key_env("art-budget"),
            "API_PROFILE_ART_BUDGET_VOLCENGINE_ACCESS_KEY_ID",
        )

    def test_admin_can_copy_profile_without_credentials_and_assign_user(self):
        main.ensure_legacy_api_profile_migration()
        request = SimpleNamespace(state=SimpleNamespace(user={"id": "admin", "role": "admin"}), query_params={})
        payload = main.AdminApiProfileCreateRequest(
            id="ui-budget",
            name="UI 经费账户",
            copy_from="legacy-shared",
        )
        with patch.object(main, "require_admin", return_value=request.state.user):
            result = asyncio.run(main.admin_create_api_profile(payload, request))
        self.assertEqual(result["profile"]["id"], "ui-budget")

        saved = main.load_api_profiles()
        copied = main.api_profile_by_id("ui-budget", saved)
        self.assertEqual(
            [item["id"] for item in copied["providers"]],
            [item["id"] for item in saved["profiles"][0]["providers"]],
        )
        serialized = json.dumps(copied, ensure_ascii=False)
        self.assertNotIn("TEST_SECRET_MUST_NOT_BE_SAVED", serialized)
        self.assertNotIn("api_key", serialized)

        update = main.AdminUserUpdateRequest(api_profile_id="ui-budget")
        with patch.object(main, "require_admin", return_value=request.state.user):
            user_result = asyncio.run(main.admin_update_user("existing", update, request))
        self.assertEqual(user_result["user"]["api_profile_id"], "ui-budget")

    def test_unassigned_user_cannot_resolve_profile(self):
        main.ensure_legacy_api_profile_migration()
        request = SimpleNamespace(
            state=SimpleNamespace(
                user={"id": "new-user", "role": "user", "api_profile_id": "", "enabled": True}
            ),
            query_params={},
        )
        with self.assertRaises(main.HTTPException) as caught:
            main.request_api_profile(request)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIsNone(main.optional_upstream_context_for_request(request, allow_default=True))

    def test_unscoped_provider_resolution_is_rejected(self):
        with self.assertRaises(main.HTTPException) as caught:
            main.get_api_provider("test-openai")
        self.assertEqual(caught.exception.status_code, 500)
        self.assertIn("UpstreamContext", caught.exception.detail)

    def test_video_generation_rejects_models_not_configured_in_current_profile(self):
        main.ensure_legacy_api_profile_migration()
        unconfigured = main.CanvasVideoRequest(
            prompt="测试",
            provider_id="test-openai",
            model="veo3-fast",
        )
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main._canvas_video_impl(unconfigured, "legacy-shared"))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("不属于当前 API 配置组", caught.exception.detail)

        provider_without_video_models = main.CanvasVideoRequest(
            prompt="测试",
            provider_id="runninghub",
            model="veo3-fast",
        )
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main._canvas_video_impl(provider_without_video_models, "legacy-shared"))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("没有为 RunningHub 配置任何视频模型", caught.exception.detail)

    def test_runninghub_video_capabilities_are_scoped_and_sanitized(self):
        main.ensure_legacy_api_profile_migration()
        data = main.load_api_profiles()
        runninghub = next(item for item in data["profiles"][0]["providers"] if item["id"] == "runninghub")
        runninghub["video_models"] = ["Seedance2.0 Image to Video"]
        main.save_api_profiles(data)
        request = SimpleNamespace(
            state=SimpleNamespace(
                user={
                    "id": "existing",
                    "role": "user",
                    "enabled": True,
                    "api_profile_id": "legacy-shared",
                }
            ),
            query_params={},
        )
        model_definition = {
            "name_en": "Seedance2.0 Image to Video",
            "name_cn": "Seedance2.0/图生视频",
            "output_type": "video",
            "params": [
                {
                    "fieldKey": "duration",
                    "type": "LIST",
                    "required": True,
                    "defaultValue": "5",
                    "options": [{"value": "4", "description": "4 秒"}, {"value": "5"}],
                    "internalSecret": "must-not-leak",
                },
                {"fieldKey": "firstFrameUrl", "type": "IMAGE", "required": True, "maxSize": 30},
            ],
        }
        with patch.object(main, "runninghub_model_definition", new=AsyncMock(return_value=model_definition)):
            result = asyncio.run(
                main.video_model_capabilities(
                    request,
                    provider_id="runninghub",
                    model="Seedance2.0 Image to Video",
                )
            )
            batch = asyncio.run(
                main.video_model_capabilities(
                    request,
                    provider_id="runninghub",
                    model="",
                )
            )

        self.assertTrue(result["discovered"])
        self.assertEqual(result["fields"][0]["default"], "5")
        self.assertEqual(result["fields"][0]["options"][0], {"value": "4", "label": "4 秒"})
        self.assertNotIn("internalSecret", json.dumps(result, ensure_ascii=False))
        self.assertEqual(len(batch["models"]), 1)
        self.assertEqual(batch["models"][0]["model"], "Seedance2.0 Image to Video")
        self.assertTrue(batch["models"][0]["discovered"])

    def test_runninghub_text_video_rejects_connected_reference_media(self):
        payload = main.CanvasVideoRequest(
            prompt="测试",
            provider_id="runninghub",
            model="Seedance2.0 Fast Text to Video",
            images=[main.AIReference(url="https://example.invalid/reference.png")],
        )
        model_definition = {
            "name_en": "Seedance2.0 Fast Text to Video",
            "endpoint": "rhart-video/sparkvideo-2.0-fast/text-to-video",
            "output_type": "video",
            "params": [
                {"fieldKey": "prompt", "type": "STRING", "required": True},
                {"fieldKey": "duration", "type": "LIST", "options": [{"value": "5"}]},
            ],
        }
        with patch.object(main, "runninghub_model_definition", new=AsyncMock(return_value=model_definition)):
            with self.assertRaises(main.HTTPException) as caught:
                asyncio.run(main.generate_runninghub_video(payload, {"id": "runninghub"}))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("文生视频", caught.exception.detail)
        self.assertIn("不会使用", caught.exception.detail)

    def test_runninghub_image_capabilities_are_scoped_and_sanitized(self):
        main.ensure_legacy_api_profile_migration()
        data = main.load_api_profiles()
        runninghub = next(item for item in data["profiles"][0]["providers"] if item["id"] == "runninghub")
        runninghub["image_models"] = ["Example Image Edit"]
        main.save_api_profiles(data)
        request = SimpleNamespace(
            state=SimpleNamespace(
                user={
                    "id": "existing",
                    "role": "user",
                    "enabled": True,
                    "api_profile_id": "legacy-shared",
                }
            ),
            query_params={},
        )
        model_definition = {
            "name_en": "Example Image Edit",
            "output_type": "image",
            "params": [
                {
                    "fieldKey": "aspectRatio",
                    "type": "LIST",
                    "required": True,
                    "defaultValue": "1:1",
                    "options": [{"value": "1:1"}, {"value": "16:9"}],
                },
                {"fieldKey": "imageUrls", "type": "IMAGE", "required": True, "multipleInputs": True, "maxInputNum": 4},
            ],
        }
        with patch.object(main, "runninghub_model_definition", new=AsyncMock(return_value=model_definition)):
            result = asyncio.run(
                main.image_model_capabilities(
                    request,
                    provider_id="runninghub",
                    model="Example Image Edit",
                )
            )
            batch = asyncio.run(
                main.image_model_capabilities(
                    request,
                    provider_id="runninghub",
                    model="",
                )
            )
        self.assertTrue(result["discovered"])
        self.assertEqual(result["fields"][1]["maxInputNum"], 4)
        self.assertEqual(batch["models"][0]["model"], "Example Image Edit")

    def test_runninghub_text_to_image_rejects_connected_reference_images(self):
        model_definition = {
            "name_en": "Example Text Image",
            "endpoint": "example/text-to-image",
            "output_type": "image",
            "params": [
                {"fieldKey": "prompt", "type": "STRING", "required": True},
                {"fieldKey": "aspectRatio", "type": "LIST", "options": [{"value": "1:1"}]},
            ],
        }
        refs = [{"url": "https://example.invalid/reference.png"}]
        with patch.object(main, "runninghub_model_definition", new=AsyncMock(return_value=model_definition)):
            with self.assertRaises(main.HTTPException) as caught:
                asyncio.run(
                    main.generate_runninghub_provider_image(
                        "测试", "1024x1024", "Example Text Image", refs, {"id": "runninghub"}
                    )
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("文生图", caught.exception.detail)
        self.assertIn("不会使用", caught.exception.detail)

    def test_runninghub_image_model_params_are_schema_filtered_and_typed(self):
        fields = [
            {"fieldKey": "prompt", "type": "STRING", "required": True},
            {"fieldKey": "chaos", "type": "INT", "min": 0, "max": 100},
            {"fieldKey": "raw", "type": "BOOLEAN"},
            {"fieldKey": "mode", "type": "LIST", "options": [{"value": "fast"}, {"value": "quality"}]},
            {"fieldKey": "imageUrl", "type": "IMAGE"},
        ]
        result = main.runninghub_image_model_params(
            fields,
            {"chaos": "42", "raw": "true", "mode": "quality", "imageUrl": "ignored"},
        )
        self.assertEqual(result, {"chaos": 42, "raw": True, "mode": "quality"})
        with self.assertRaises(main.HTTPException) as unknown:
            main.runninghub_image_model_params(fields, {"watermark": True})
        self.assertEqual(unknown.exception.status_code, 400)
        with self.assertRaises(main.HTTPException) as out_of_range:
            main.runninghub_image_model_params(fields, {"chaos": 101})
        self.assertIn("不能大于", out_of_range.exception.detail)

    def test_same_provider_id_resolves_distinct_profile_credentials_without_fallback(self):
        main.ensure_legacy_api_profile_migration()
        data = main.load_api_profiles()
        provider = {
            "id": "test-openai",
            "name": "测试平台",
            "base_url": "https://example.invalid/v1",
            "protocol": "openai",
            "enabled": True,
            "primary": True,
            "image_models": ["image-test"],
        }
        data["profiles"].extend(
            [
                {"id": "test-ui", "name": "UI 测试账户", "enabled": True, "providers": [provider]},
                {"id": "test-art", "name": "原画测试账户", "enabled": True, "providers": [provider]},
            ]
        )
        main.save_api_profiles(data)
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("API_PROFILE_TEST_UI_PROVIDER_TEST_OPENAI_KEY=TEST_KEY_UI_ONLY\n")
            f.write("API_PROFILE_TEST_ART_PROVIDER_TEST_OPENAI_KEY=TEST_KEY_ART_ONLY\n")

        ui_provider = main.get_api_provider("test-openai", "test-ui")
        art_provider = main.get_api_provider("test-openai", "test-art")
        self.assertEqual(main.api_headers(provider=ui_provider)["Authorization"], "Bearer TEST_KEY_UI_ONLY")
        self.assertEqual(main.api_headers(provider=art_provider)["Authorization"], "Bearer TEST_KEY_ART_ONLY")

        default_ui = main.get_api_provider("", "test-ui")
        self.assertEqual(default_ui["id"], "test-openai")
        with self.assertRaises(main.HTTPException) as caught:
            main.get_api_provider("only-in-another-profile", "test-ui")
        self.assertEqual(caught.exception.status_code, 400)

    def test_upstream_context_snapshots_profile_provider_and_credentials(self):
        main.ensure_legacy_api_profile_migration()
        data = main.load_api_profiles()
        provider = {
            "id": "test-openai",
            "name": "测试平台",
            "base_url": "https://example.invalid/v1",
            "protocol": "openai",
            "enabled": True,
            "primary": True,
            "image_models": ["image-test"],
        }
        data["profiles"].extend(
            [
                {"id": "ctx-ui", "name": "UI 上下文", "enabled": True, "providers": [provider]},
                {"id": "ctx-art", "name": "原画上下文", "enabled": True, "providers": [provider]},
            ]
        )
        main.save_api_profiles(data)
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("API_PROFILE_CTX_UI_PROVIDER_TEST_OPENAI_KEY=CTX_UI_SECRET\n")
            f.write("API_PROFILE_CTX_ART_PROVIDER_TEST_OPENAI_KEY=CTX_ART_SECRET\n")

        ui = main.upstream_context_for_profile_id("ctx-ui", "test-openai", user_id="ui-user")
        art = main.upstream_context_for_profile_id("ctx-art", "test-openai", user_id="art-user")

        self.assertEqual(ui.user_id, "ui-user")
        self.assertEqual(ui.api_profile_id, "ctx-ui")
        self.assertEqual(ui.provider["_api_profile_id"], "ctx-ui")
        self.assertEqual(ui.api_key, "CTX_UI_SECRET")
        self.assertEqual(art.api_key, "CTX_ART_SECRET")
        self.assertNotEqual(ui.api_key, art.api_key)

    def test_runninghub_workflow_store_migrates_legacy_without_cross_profile_leak(self):
        legacy_cfg = {
            "legacy-workflow": {
                "workflowId": "legacy-workflow",
                "fields": [{"id": "field-1"}],
            }
        }
        with open(self.runninghub_workflow_store_path, "w", encoding="utf-8") as f:
            json.dump(legacy_cfg, f)

        self.assertIn(
            "legacy-workflow",
            main.load_runninghub_workflow_store(main.LEGACY_API_PROFILE_ID),
        )
        self.assertEqual(main.load_runninghub_workflow_store("art-profile"), {})

        art_cfg = {
            "art-workflow": {
                "workflowId": "art-workflow",
                "fields": [{"id": "field-2"}],
            }
        }
        main.save_runninghub_workflow_store(art_cfg, "art-profile")

        self.assertIn(
            "legacy-workflow",
            main.load_runninghub_workflow_store(main.LEGACY_API_PROFILE_ID),
        )
        self.assertEqual(
            list(main.load_runninghub_workflow_store("art-profile")),
            ["art-workflow"],
        )
        with open(self.runninghub_workflow_store_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["version"], 2)
        self.assertEqual(
            set(saved["profiles"]),
            {main.LEGACY_API_PROFILE_ID, "art-profile"},
        )

    def test_paid_provider_calls_cannot_use_legacy_unscoped_resolvers(self):
        tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name in {"get_api_provider", "get_api_provider_exact"}:
                has_profile_kw = any(keyword.arg == "api_profile_id" for keyword in node.keywords)
                if len(node.args) < 2 and not has_profile_kw:
                    violations.append((name, node.lineno))
            elif name == "api_headers":
                has_provider_kw = any(keyword.arg == "provider" for keyword in node.keywords)
                if len(node.args) < 2 and not has_provider_kw:
                    violations.append((name, node.lineno))
            elif name == "provider_env_key_value" and len(node.args) < 2:
                violations.append((name, node.lineno))
            elif name == "modelscope_api_key" and node.args:
                violations.append((name, node.lineno))
            elif name in {"volcengine_access_key_value", "volcengine_secret_key_value"} and not node.args:
                violations.append((name, node.lineno))

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
