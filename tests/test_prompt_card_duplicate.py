"""提示词库“我的提示词”卡片新增的说明条与“创建副本”按钮。

用户可见改动有两处（均为卡片悬停态）：
* 封面底部显示提示词说明，最多两行，超出部分用省略号；
* 封面右上角的小按钮“创建副本”，复制出一条新的提示词用于试微调，原版保持不变。

前端部分按仓库惯例做静态契约断言（读取 js/css/i18n 源文件），
后端部分直接调用 ``add_prompt_library_item`` 验证“副本”复用的接口行为：
新 id、内容一致、原版保留、新项排在最前、越权被拒、空提示词被拒。
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main


PROMPT_JS = (ROOT / "static" / "js" / "prompt-library.js").read_text(encoding="utf-8")
PROMPT_CSS = (ROOT / "static" / "css" / "prompt-library.css").read_text(encoding="utf-8")
LIBRARY_I18N = (ROOT / "static" / "js" / "i18n" / "library.js").read_text(encoding="utf-8")
LUCIDE_VENDOR = (ROOT / "static" / "vendor" / "js" / "lucide.js").read_text(encoding="utf-8")


class PromptCardMarkupContractTests(unittest.TestCase):
    """卡片模板与交互钩子必须按约定渲染（仅“我的提示词”）。"""

    def test_cover_renders_description_strip_for_my_prompts(self):
        self.assertIn("const description = String(item.description || item.scene || '').trim();", PROMPT_JS)
        self.assertIn("const descriptionNote = isMine && description", PROMPT_JS)
        self.assertIn('<p class="prompt-card-desc">', PROMPT_JS)
        # 说明条在封面容器内部（与 1 号区域一致：缩略图底部横带）。
        self.assertIn("</button></div>${descriptionNote}${duplicateButton}</div>", PROMPT_JS)

    def test_cover_renders_duplicate_button_for_my_prompts(self):
        self.assertIn("const duplicateButton = isMine ?", PROMPT_JS)
        self.assertIn('class="prompt-card-duplicate"', PROMPT_JS)
        self.assertIn('data-pl-duplicate="${LibraryUtils.escapeHtml(item.id)}"', PROMPT_JS)
        self.assertIn('data-lucide="copy-plus"', PROMPT_JS)
        # 图标必须真实存在于本地 lucide 包（kebab-case 会映射为 CopyPlus）。
        self.assertIn("CopyPlus", LUCIDE_VENDOR)

    def test_duplicate_button_is_wired_in_click_handler(self):
        self.assertIn("event.target.closest('[data-pl-duplicate]')", PROMPT_JS)
        self.assertIn(
            "await this.duplicateItem(duplicate.dataset.plDuplicate, duplicate)",
            PROMPT_JS,
        )
        # 与“复制组合提示词”（data-pl-copy）互不干扰。
        self.assertNotIn("data-pl-duplicate", "".join(
            line for line in PROMPT_JS.splitlines() if "data-pl-copy" in line
        ))

    def test_duplicate_reuses_item_endpoint_and_copies_content(self):
        self.assertIn("fetch('/api/prompt-libraries/items', {method:'POST'", PROMPT_JS)
        for field in ("category:", "subcategory:", "description:", "cover_url:", "prefix:", "suffix:"):
            self.assertIn(field, PROMPT_JS)
        # 复制失败要提示，成功要刷新列表。
        self.assertIn("t('library.duplicateFailed', '创建副本失败')", PROMPT_JS)
        self.assertIn("t('library.duplicateDone', '已创建提示词副本')", PROMPT_JS)
        self.assertIn("await this.load();", PROMPT_JS)

    def test_duplicate_name_appends_copy_suffix_and_avoids_collisions(self):
        self.assertIn("t('library.duplicateSuffix', '副本')", PROMPT_JS)
        self.assertIn("`${stem} ${suffix}`.slice(0, 120)", PROMPT_JS)
        self.assertIn("taken.has(name)", PROMPT_JS)
        self.assertIn("index <= 99", PROMPT_JS)

    def test_card_styles_reveal_on_hover_with_two_line_ellipsis(self):
        self.assertIn(".prompt-card-duplicate {", PROMPT_CSS)
        self.assertIn(".prompt-card-desc {", PROMPT_CSS)
        self.assertIn("-webkit-line-clamp: 2", PROMPT_CSS)
        self.assertIn(
            ".prompt-card:hover .prompt-card-duplicate, .prompt-card:focus-within .prompt-card-duplicate,",
            PROMPT_CSS,
        )
        self.assertIn(
            ".prompt-card:hover .prompt-card-desc, .prompt-card:focus-within .prompt-card-desc { opacity: 1; transform: none; }",
            PROMPT_CSS,
        )
        # 触屏没有 hover，两个新元素必须常显，否则点不到副本按钮。
        self.assertIn(
            ".prompt-card-duplicate, .prompt-card-desc { opacity: 1; transform: none; }",
            PROMPT_CSS,
        )

    def test_i18n_covers_new_prompt_copy_strings(self):
        for key, zh, en in (
            ("library.duplicatePrompt", "创建副本", "Create a copy"),
            ("library.duplicateHint", "创建副本，用于试验提示词微调", "Create a copy to experiment with prompt tweaks"),
            ("library.duplicateSuffix", "副本", "Copy"),
            ("library.duplicateDone", "已创建提示词副本", "Prompt copy created"),
            ("library.duplicateFailed", "创建副本失败", "Failed to create a copy"),
        ):
            self.assertIn(f'"{key}": {{ zh: "{zh}", en: "{en}" }}', LIBRARY_I18N)


class PromptCardDuplicateEndpointTests(unittest.TestCase):
    """“创建副本”复用的后端接口行为。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prompt_path = os.path.join(self.temp_dir.name, "prompt_libraries.json")
        self.trash_path = os.path.join(self.temp_dir.name, "asset_trash.json")
        self.users = {"users": [{"id": "alice", "username": "Alice"}, {"id": "bob", "username": "Bob"}]}
        self.patchers = [
            patch.object(main, "PROMPT_LIBRARY_PATH", self.prompt_path),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_DIR", os.path.join(self.temp_dir.name, "published")),
            patch.object(main, "PROMPT_LIBRARY_WITHDRAWN_DIR", os.path.join(self.temp_dir.name, "withdrawn")),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_COVER_DIR", os.path.join(self.temp_dir.name, "covers")),
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
                        "name": "场景拆分",
                        "category": "other",
                        "description": "把一句话拆成多个镜头",
                        "cover_url": "/assets/covers/scene.png",
                        "prefix": "拆分场景：",
                        "suffix": "输出分镜表格",
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
                    "items": [],
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

    def stored_items(self, library_id):
        data = main.load_prompt_libraries()
        library = next(item for item in data["libraries"] if item["id"] == library_id)
        return library["items"]

    def duplicate_payload(self, **overrides):
        payload = {
            "library_id": "prompt_alice",
            "name": "场景拆分 副本",
            "category": "other",
            "subcategory": "",
            "description": "把一句话拆成多个镜头",
            "cover_url": "/assets/covers/scene.png",
            "prefix": "拆分场景：",
            "suffix": "输出分镜表格",
        }
        payload.update(overrides)
        return main.PromptLibraryItemRequest(**payload)

    def test_duplicate_creates_new_item_and_keeps_original(self):
        with patch.object(main, "require_authenticated", return_value=self.alice):
            result = asyncio.run(main.add_prompt_library_item(self.duplicate_payload(), object()))

        duplicated = result["item"]
        self.assertNotEqual(duplicated["id"], "alice_prompt")
        self.assertTrue(duplicated["id"].startswith("tpl_"))
        self.assertEqual(duplicated["name"], "场景拆分 副本")
        self.assertEqual(duplicated["description"], "把一句话拆成多个镜头")
        self.assertEqual(duplicated["prefix"], "拆分场景：")
        self.assertEqual(duplicated["suffix"], "输出分镜表格")
        self.assertEqual(duplicated["cover_url"], "/assets/covers/scene.png")
        self.assertEqual(duplicated["owner_id"], "alice")

        items = self.stored_items("prompt_alice")
        self.assertEqual([item["id"] for item in items], [duplicated["id"], "alice_prompt"])
        self.assertEqual(items[1]["prefix"], "拆分场景：")

    def test_duplicate_into_foreign_library_is_rejected(self):
        with patch.object(main, "require_authenticated", return_value=self.bob):
            with self.assertRaises(main.HTTPException) as error:
                asyncio.run(main.add_prompt_library_item(self.duplicate_payload(), object()))
        self.assertIn(error.exception.status_code, (403, 404))
        self.assertEqual(len(self.stored_items("prompt_alice")), 1)

    def test_duplicate_without_prefix_or_suffix_is_rejected(self):
        with patch.object(main, "require_authenticated", return_value=self.alice):
            with self.assertRaises(main.HTTPException) as error:
                asyncio.run(main.add_prompt_library_item(
                    self.duplicate_payload(prefix="", suffix="", positive="", negative=""),
                    object(),
                ))
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
