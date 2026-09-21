import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class DepartmentManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.users_path = os.path.join(self.temp_dir.name, "auth_users.json")
        self.departments_path = os.path.join(self.temp_dir.name, "departments.json")
        # auth_register() → ensure_user_asset_spaces() 会给新用户建个人资产库/提示词库。
        # 不隔离这两个路径，跑一次注册用例就往仓库的 data/*.json 里塞一个
        # 「测试用户的资产库」，asset_library.json 是跟踪文件会脏工作区、
        # prompt_libraries.json 被 .gitignore 忽略则悄悄累积。见下方
        # test_registration_does_not_write_the_repository_data_files 的回归防线。
        self.asset_library_path = os.path.join(self.temp_dir.name, "asset_library.json")
        self.prompt_library_path = os.path.join(self.temp_dir.name, "prompt_libraries.json")
        with open(self.users_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "users": [
                        {
                            "id": "legacy-user",
                            "username": "legacy",
                            "name": "旧用户",
                            "department": "UI",
                            "role": "user",
                            "enabled": True,
                        },
                        {
                            "id": "case-mismatch-user",
                            "username": "case-mismatch",
                            "name": "大小写历史数据",
                            "department": "ui",
                            "role": "user",
                            "enabled": True,
                        },
                        {
                            "id": "admin",
                            "username": "admin",
                            "name": "管理员",
                            "department": "管理",
                            "role": "admin",
                            "enabled": True,
                        },
                    ]
                },
                handle,
                ensure_ascii=False,
            )
        self.patchers = [
            patch.object(main, "AUTH_USERS_FILE", self.users_path),
            patch.object(main, "DEPARTMENTS_FILE", self.departments_path),
            patch.object(main, "ASSET_LIBRARY_PATH", self.asset_library_path),
            patch.object(main, "PROMPT_LIBRARY_PATH", self.prompt_library_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.request = SimpleNamespace(state=SimpleNamespace(user={"id": "admin", "role": "admin"}))
        self.admin_patch = patch.object(main, "require_admin", return_value=self.request.state.user)
        self.admin_patch.start()

    def tearDown(self):
        self.admin_patch.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_legacy_user_departments_are_bootstrapped_but_admin_label_is_not(self):
        rows = main.load_departments()["departments"]

        self.assertEqual([row["name"] for row in rows], ["UI"])
        self.assertTrue(rows[0]["enabled"])

    def test_department_count_does_not_merge_case_mismatched_legacy_labels(self):
        result = asyncio.run(main.admin_departments(self.request))

        self.assertEqual(result["departments"][0]["name"], "UI")
        self.assertEqual(result["departments"][0]["assigned_users"], 1)
        self.assertEqual(result["unassigned_users"], 1)

    def test_registration_rejects_free_text_and_stores_stable_department_id(self):
        department = main.load_departments()["departments"][0]
        invalid = main.AuthRegisterRequest(
            username="invalid-user",
            password="a-secure-password",
            name="测试用户",
            department="随便填写",
        )
        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.auth_register(invalid))
        self.assertEqual(caught.exception.status_code, 400)

        valid = main.AuthRegisterRequest(
            username="valid-user",
            password="a-secure-password",
            name="测试用户",
            department=department["id"],
        )
        result = asyncio.run(main.auth_register(valid))

        self.assertEqual(result["user"]["department"], "UI")
        self.assertEqual(result["user"]["department_id"], department["id"])

    def test_admin_can_create_rename_and_disable_department(self):
        created = asyncio.run(
            main.admin_create_department(main.AdminDepartmentCreateRequest(name="原画"), self.request)
        )["department"]
        renamed = asyncio.run(
            main.admin_update_department(
                created["id"],
                main.AdminDepartmentUpdateRequest(name="原画设计", enabled=False),
                self.request,
            )
        )["department"]

        self.assertEqual(renamed["name"], "原画设计")
        self.assertFalse(renamed["enabled"])
        with self.assertRaises(main.HTTPException) as caught:
            main.resolve_department(created["id"])
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(main.resolve_department(created["id"], include_disabled=True)["name"], "原画设计")

    def test_rename_migrates_legacy_users_and_assigned_department_cannot_be_deleted(self):
        department = main.load_departments()["departments"][0]
        asyncio.run(
            main.admin_update_department(
                department["id"],
                main.AdminDepartmentUpdateRequest(name="UI 设计"),
                self.request,
            )
        )
        with open(self.users_path, "r", encoding="utf-8") as handle:
            user = json.load(handle)["users"][0]
        self.assertEqual(user["department"], "UI 设计")
        self.assertEqual(user["department_id"], department["id"])

        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.admin_delete_department(department["id"], self.request))
        self.assertEqual(caught.exception.status_code, 409)

    def test_registration_does_not_write_the_repository_data_files(self):
        """回归防线：注册用例只能在临时目录里建个人空间。

        这个文件曾经漏掉 ASSET_LIBRARY_PATH / PROMPT_LIBRARY_PATH，
        于是 `-m unittest discover -s tests` 每跑一次就往仓库 data/ 里塞一个
        「测试用户的资产库」（asset_library.json 是跟踪文件，工作区变脏）和一个
        「测试用户的提示词库」（prompt_libraries.json 被忽略，悄悄累积）。这里直接
        比对仓库文件字节，把隔离去掉就会红。
        """
        root = Path(__file__).resolve().parents[1]
        repo_files = [
            root / "data" / "asset_library.json",
            root / "data" / "prompt_libraries.json",
        ]
        before = {path: (path.read_bytes() if path.exists() else None) for path in repo_files}

        department = main.load_departments()["departments"][0]
        asyncio.run(
            main.auth_register(
                main.AuthRegisterRequest(
                    username="isolation-user",
                    password="a-secure-password",
                    name="测试用户",
                    department=department["id"],
                )
            )
        )

        after = {path: (path.read_bytes() if path.exists() else None) for path in repo_files}
        self.assertEqual(after, before, "注册用例写进了仓库的 data/ 文件")

        # 个人空间确实建了，只是建在临时目录里——不是靠「跳过建库」冒充隔离。
        with open(self.asset_library_path, "r", encoding="utf-8") as handle:
            libraries = json.load(handle)["libraries"]
        self.assertIn("测试用户的资产库", [row.get("name") for row in libraries])
        with open(self.prompt_library_path, "r", encoding="utf-8") as handle:
            prompt_libraries = json.load(handle)["libraries"]
        self.assertIn("测试用户的提示词库", [row.get("name") for row in prompt_libraries])


if __name__ == "__main__":
    unittest.main()
