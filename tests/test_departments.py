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


if __name__ == "__main__":
    unittest.main()
