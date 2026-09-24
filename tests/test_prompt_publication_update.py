"""「更新发布」：原提示词改动后，公开版本要能一键跟上。

用户要求（本轮）：我的提示词里，如果一条提示词已经发布过、之后又改了内容，
卡片上的「已发布」要变成「更新发布」，用与「已发布」不同的颜色区分，
点击后按字面意思快速更新已发布的内容。

服务端语义：
* 陈旧判定 = 公开版本镜像的正文（prefix / suffix / description / params）与源不一致，
  或源在公开版本上次刷新之后又被保存过；公开名称、类别、封面是发布时特意选定的，
  不参与判定；
* 「更新发布」＝对同一个发布接口再发一次 ``{published: true}``，服务端原地刷新同一条快照
  （保持 id、公开名称、类别、封面文件名与首次公开时间），不产生第二张卡片；
* 判定结果只注解给当前用户自己的卡片（``publication_outdated``），不泄漏给他人视图。

覆盖三类断言：
* 行为验证：直接调用 main 的发布 / 编辑接口，验证原地刷新、封面保留、legacy 层不复制；
* 客户端契约：用 node 在 vm 中加载真实的 prompt-library.js，渲染卡片断言按钮的分支与转义；
* 静态契约：CSS 的「更新发布」配色必须区别于「已发布」，i18n 词条中英齐全。
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main  # noqa: E402  (importing main only reads it: the sandbox runner patches mkdtemp)


PROMPT_JS = (ROOT / "static" / "js" / "prompt-library.js").read_text(encoding="utf-8")
PROMPT_CSS = (ROOT / "static" / "css" / "prompt-library.css").read_text(encoding="utf-8")
LIBRARY_I18N = (ROOT / "static" / "js" / "i18n" / "library.js").read_text(encoding="utf-8")

NODE = shutil.which("node")

# 在 vm 里加载真实模块并渲染卡片：脚本从 stdin 读入，因此不需要临时文件。
NODE_HARNESS = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const code = fs.readFileSync(path.join(process.cwd(), 'static', 'js', 'prompt-library.js'), 'utf8');
const sandbox = {
    console,
    window: {},
    document: {
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement() { return {}; },
    },
    requestAnimationFrame() {},
    CSS: { escape: value => String(value) },
    t: (key, fallback) => (fallback === undefined ? key : fallback),
    LibraryUtils: {
        escapeHtml: value => String(value).replace(/[&<>"']/g, ch => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
        )),
        truncate: (value, limit) => String(value).slice(0, limit),
    },
};

const PromptLibrary = vm.runInNewContext(`${code}\n;PromptLibrary;`, sandbox);

const base = { name: '场景拆分', prefix: '镜头级前缀', suffix: '后缀', description: '说明' };
const variants = {
    outdated: { tab: 'myPrompts', publication: { id: 'published_1' }, item: { ...base, id: 'p1', publication_outdated: true } },
    current: { tab: 'myPrompts', publication: { id: 'published_1' }, item: { ...base, id: 'p1', publication_outdated: false } },
    flagWithoutPublication: { tab: 'myPrompts', publication: null, item: { ...base, id: 'p2', publication_outdated: true } },
    unpublished: { tab: 'myPrompts', publication: null, item: { ...base, id: 'p3' } },
    otherTab: { tab: 'inspiration', publication: null, item: { ...base, id: 'p1', publication_outdated: true } },
    escaping: { tab: 'myPrompts', publication: { id: 'published_1' }, item: { ...base, id: '<img src=x onerror=1>', publication_outdated: true } },
};

const out = {};
for (const [key, variant] of Object.entries(variants)) {
    const view = Object.create(PromptLibrary);
    Object.assign(view, {
        tab: variant.tab,
        favorites: new Set(),
        activeTarget: () => null,
        publishedForSource: () => variant.publication,
    });
    out[key] = view.cardHtml(variant.item);
}
process.stdout.write(JSON.stringify(out));
"""


def _rule_body(css, selector):
    """Return the declarations of the first rule whose selector matches exactly."""
    match = re.search(r"(?:^|\n)%s\s*\{([^}]*)\}" % re.escape(selector), css)
    if not match:
        raise AssertionError(f"CSS rule not found: {selector}")
    return match.group(1)


class PromptPublicationStalenessTests(unittest.TestCase):
    """陈旧判定只关心公开版本镜像的正文，以及源是否在刷新之后又被保存过。"""

    def test_public_metadata_differences_alone_are_not_stale(self):
        source = {"prefix": "a", "suffix": "b", "description": "d", "updated_at": 2000}
        snapshot = {
            "prefix": "a", "suffix": "b", "description": "d", "updated_at": 2000,
            # 发布时特意选定的公开名称/类别，以及被重编码到 published/ 的封面。
            "name": "公开名称", "category": "filter", "subcategory": "color",
            "cover_url": "/static/images/prompt-library/published/published_1_cover.webp",
        }
        self.assertFalse(main.prompt_publication_needs_update(source, snapshot))

    def test_edited_content_is_stale(self):
        snapshot = {"prefix": "a", "suffix": "b", "description": "d", "params": {"steps": 20}, "updated_at": 5000}
        for changes in (
            {"prefix": "a2"},
            {"suffix": "b2"},
            {"description": "d2"},
            {"params": {"steps": 30}},
        ):
            with self.subTest(changes=changes):
                source = {**snapshot, **changes}
                self.assertTrue(main.prompt_publication_needs_update(source, snapshot))

    def test_a_newer_source_timestamp_is_stale_even_with_identical_content(self):
        snapshot = {"prefix": "a", "description": "d", "updated_at": 5000}
        self.assertTrue(main.prompt_publication_needs_update({**snapshot, "updated_at": 5001}, snapshot))
        self.assertFalse(main.prompt_publication_needs_update({**snapshot, "updated_at": 5000}, snapshot))
        self.assertFalse(main.prompt_publication_needs_update({**snapshot, "updated_at": 4999}, snapshot))

    def test_params_order_does_not_matter(self):
        snapshot = {"prefix": "a", "params": {"seed": 1, "steps": 20}, "updated_at": 10}
        source = {**snapshot, "params": {"steps": 20, "seed": 1}}
        self.assertFalse(main.prompt_publication_needs_update(source, snapshot))


class PromptPublicationUpdateTests(unittest.TestCase):
    """原地刷新的端到端行为。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prompt_path = os.path.join(self.temp_dir.name, "prompt_libraries.json")
        self.trash_path = os.path.join(self.temp_dir.name, "asset_trash.json")
        self.versioned_dir = os.path.join(self.temp_dir.name, "prompt-library-published")
        self.withdrawn_dir = os.path.join(self.temp_dir.name, "prompt-library-withdrawn")
        self.versioned_cover_dir = os.path.join(self.temp_dir.name, "published-covers")
        self.users = {"users": [{"id": "alice", "username": "Alice"}, {"id": "bob", "username": "Bob"}]}
        self.patchers = [
            patch.object(main, "PROMPT_LIBRARY_PATH", self.prompt_path),
            patch.object(main, "PROMPT_LIBRARY_PUBLISHED_DIR", self.versioned_dir),
            patch.object(main, "PROMPT_LIBRARY_WITHDRAWN_DIR", self.withdrawn_dir),
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
                        "description": "原始说明",
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

    # --- helpers -----------------------------------------------------------

    def publish(self, user=None, **metadata):
        user = user or self.alice
        with patch.object(main, "require_authenticated", return_value=user):
            return asyncio.run(main.publish_prompt_library_item(
                "alice_prompt", main.PromptLibraryPublishRequest(published=True, **metadata), object()))

    def edit_source(self, **changes):
        """Simulate the editor: the PATCH endpoint bumps updated_at on every save."""
        data = main.load_prompt_libraries()
        item = self.source_item(data)
        item.update(changes)
        item["updated_at"] = main.now_ms()
        main.save_prompt_libraries(data)
        return item

    @staticmethod
    def source_item(data):
        library = next(lib for lib in data["libraries"] if lib["id"] == "prompt_alice")
        return library["items"][0]

    def owner_item(self, user=None):
        view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), user or self.alice)
        library = next(lib for lib in view["libraries"] if lib["id"] == "prompt_alice")
        return library["items"][0]

    def mark_outdated_by_edit(self):
        return self.edit_source(prefix="alice prefix v2", description="改过的说明")

    # --- annotation --------------------------------------------------------

    def test_an_unpublished_prompt_carries_no_outdated_flag(self):
        self.assertNotIn("publication_outdated", self.owner_item())

    def test_a_fresh_publication_is_not_outdated_and_an_edit_marks_it(self):
        self.publish()
        self.assertIs(self.owner_item()["publication_outdated"], False)

        self.mark_outdated_by_edit()
        self.assertIs(self.owner_item()["publication_outdated"], True)

    def test_the_flag_is_not_leaked_to_other_viewers(self):
        self.publish()
        self.mark_outdated_by_edit()

        for user in (self.bob, {"id": "admin", "username": "Admin", "role": "admin"}):
            view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), user)
            for library in view["libraries"]:
                for item in library["items"]:
                    self.assertNotIn("publication_outdated", item)
            for item in view["published"] + view["inspiration"]:
                self.assertNotIn("publication_outdated", item)

    # --- in-place refresh --------------------------------------------------

    def test_republish_refreshes_the_same_snapshot_and_keeps_the_public_metadata(self):
        first = self.publish(name="公开电影肖像", category="filter", subcategory="color")["snapshot"]
        self.edit_source(prefix="alice prefix v2", description="改过的说明")

        refreshed = self.publish()["snapshot"]
        self.assertEqual(refreshed["id"], first["id"])
        self.assertEqual(refreshed["prefix"], "alice prefix v2")
        self.assertEqual(refreshed["description"], "改过的说明")
        # 公开名称与类别是发布时选定的，快速更新不得改动它们。
        self.assertEqual(refreshed["name"], "公开电影肖像")
        self.assertEqual(refreshed["category"], "filter")
        self.assertEqual(refreshed["subcategory"], "color")
        # 首次公开时间保留，刷新只推进 updated_at（陈旧判定比的就是它）。
        self.assertEqual(refreshed["published_at"], first["published_at"])
        self.assertGreaterEqual(refreshed["updated_at"], first["updated_at"])

        self.assertIs(self.owner_item()["publication_outdated"], False)
        self.assertEqual([item["id"] for item in main.load_versioned_published_prompts()], [first["id"]])
        view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), self.alice)
        self.assertEqual([item["id"] for item in view["published"]], [first["id"]])

    def test_the_public_of_the_refresh_is_visible_to_other_users(self):
        self.publish()
        self.mark_outdated_by_edit()
        self.publish()

        bob_view = main.public_prompt_libraries_for_user(main.load_prompt_libraries(), self.bob)
        inspiration = next(item for item in bob_view["inspiration"] if item["source_prompt_id"] == "alice_prompt")
        self.assertEqual(inspiration["prefix"], "alice prefix v2")
        self.assertEqual(inspiration["description"], "改过的说明")

    def test_refresh_keeps_a_publication_that_lives_in_the_legacy_runtime_layer(self):
        snapshot_id = self.seed_legacy_runtime_publication()
        self.mark_outdated_by_edit()

        refreshed = self.publish()["snapshot"]
        self.assertEqual(refreshed["id"], snapshot_id)
        self.assertEqual(refreshed["prefix"], "alice prefix v2")
        # 依然只有一条记录：刷新不得把 legacy 层复制进 tracked catalog。
        self.assertEqual(main.load_versioned_published_prompts(), [])
        runtime = main.load_prompt_libraries()["published"]
        self.assertEqual([item["id"] for item in runtime], [snapshot_id])
        self.assertEqual(runtime[0]["prefix"], "alice prefix v2")

    def test_refresh_keeps_the_published_cover_when_the_source_has_none(self):
        cover = os.path.join(self.temp_dir.name, "cover.png")
        Image.new("RGB", (800, 450), (10, 20, 30)).save(cover)
        with patch.object(main, "output_file_from_url", return_value=cover):
            first = self.publish()["snapshot"]
        cover_url = first["cover_url"]
        self.assertTrue(cover_url.startswith("/static/images/prompt-library/published/"))
        cover_path = os.path.join(self.versioned_cover_dir, os.path.basename(cover_url))
        self.assertTrue(os.path.isfile(cover_path))

        self.edit_source(prefix="alice prefix v2", cover_url="")
        refreshed = self.publish()["snapshot"]

        self.assertEqual(refreshed["cover_url"], cover_url)
        self.assertTrue(os.path.isfile(cover_path))

    def test_refresh_overwrites_the_cover_file_in_place_when_the_source_cover_changes(self):
        old_cover = os.path.join(self.temp_dir.name, "old.png")
        new_cover = os.path.join(self.temp_dir.name, "new.png")
        Image.new("RGB", (800, 450), (10, 20, 30)).save(old_cover)
        Image.new("RGB", (800, 450), (200, 120, 40)).save(new_cover)

        with patch.object(main, "output_file_from_url", return_value=old_cover):
            first = self.publish()["snapshot"]
        cover_path = os.path.join(self.versioned_cover_dir, os.path.basename(first["cover_url"]))
        with open(cover_path, "rb") as handle:
            old_bytes = handle.read()

        self.edit_source(prefix="alice prefix v2", cover_url="/assets/uploads/new.png")
        with patch.object(main, "output_file_from_url", return_value=new_cover):
            refreshed = self.publish()["snapshot"]

        # 同一个快照沿用同名封面文件：不产生孤儿文件，也不会误删别的快照封面。
        self.assertEqual(refreshed["cover_url"], first["cover_url"])
        with open(cover_path, "rb") as handle:
            new_bytes = handle.read()
        self.assertNotEqual(new_bytes, old_bytes)
        self.assertEqual(sorted(os.listdir(self.versioned_cover_dir)), [os.path.basename(cover_path)])

    def test_an_explicit_metadata_request_can_still_rename_a_publication(self):
        self.publish(name="旧公开名", category="style", subcategory="real")
        self.mark_outdated_by_edit()

        refreshed = self.publish(name="新公开名", category="filter", subcategory="film")["snapshot"]
        self.assertEqual(refreshed["name"], "新公开名")
        self.assertEqual(refreshed["category"], "filter")
        self.assertEqual(refreshed["subcategory"], "film")

    def test_refresh_still_validates_requested_metadata(self):
        self.publish()
        with self.assertRaises(main.HTTPException) as error:
            self.publish(category="custom")
        self.assertEqual(error.exception.status_code, 400)

    def test_withdraw_still_removes_the_refreshed_publication(self):
        self.publish()
        self.mark_outdated_by_edit()
        snapshot_id = self.publish()["snapshot"]["id"]

        with patch.object(main, "require_authenticated", return_value=self.alice):
            withdrawn = asyncio.run(main.withdraw_prompt_library_snapshot(snapshot_id, object()))

        self.assertTrue(withdrawn["withdrawn"])
        self.assertNotIn("publication_outdated", self.owner_item())
        self.assertEqual(main.load_prompt_libraries()["published"], [])
        self.assertEqual(main.load_versioned_published_prompts(), [])

    def seed_legacy_runtime_publication(self, snapshot_id="published_legacy_0001", owner="alice"):
        """Reproduce a publication created before the versioned catalog existed."""
        data = main.load_prompt_libraries()
        data["published"] = [{
            "id": snapshot_id,
            "name": "Alice 旧发布",
            "prefix": "alice prefix",
            "source_prompt_id": "alice_prompt",
            "source_author_id": owner,
            "published": True,
            "published_at": 1700000000000,
            "owner_type": "user",
            "owner_id": owner,
        }]
        main.save_prompt_libraries(data)
        return snapshot_id


class PromptPublicationClientContractTests(unittest.TestCase):
    """客户端只负责“怎么显示、点哪里”，配对的服务端语义由上面的行为测试锁定。"""

    def test_the_card_wires_the_outdated_button_to_the_publish_endpoint(self):
        self.assertIn("async updatePublication(id) {", PROMPT_JS)
        self.assertIn(
            "fetch(`/api/prompt-libraries/items/${encodeURIComponent(id)}/publish`, "
            "{method:'PATCH', headers:{'Content-Type':'application/json'}, "
            "body:JSON.stringify({published:true})})",
            PROMPT_JS,
        )
        # 点击必须走 withBusy（禁用按钮 + 转圈），与发布/撤回一致。
        self.assertIn(
            "this.withBusy(updatePublication,()=>this.updatePublication("
            "updatePublication.dataset.plUpdatePublication))",
            PROMPT_JS,
        )
        # 更新分支要排在“跳到我的发布”之前，否则旧行为会吃掉这次点击。
        self.assertLess(
            PROMPT_JS.index("data-pl-update-publication]')"),
            PROMPT_JS.index("data-pl-show-publication]')"),
        )

    def test_the_outdated_state_is_reported_to_the_user(self):
        self.assertIn("t('library.publishUpdatedToast', '已更新发布内容')", PROMPT_JS)
        self.assertIn("t('library.updatePublishFailed', '更新发布失败')", PROMPT_JS)

    def test_update_button_uses_a_colour_that_differs_from_published(self):
        published = _rule_body(PROMPT_CSS, ".prompt-card-manage button.is-published")
        outdated = _rule_body(PROMPT_CSS, ".prompt-card-manage button.is-published.is-outdated")
        self.assertNotEqual(published.strip(), outdated.strip())
        # 已发布是品牌 lime，更新发布必须是另一套颜色（琥珀告警色）。
        self.assertIn("199,255,0", published)
        self.assertNotIn("199,255,0", outdated)
        self.assertIn("255,186,73", outdated)
        # 悬停 / 键盘聚焦也要保持这套颜色，否则会被通用 hover 规则盖回绿色。
        hover = _rule_body(
            PROMPT_CSS,
            ".prompt-card-manage button.is-published.is-outdated:hover, "
            ".prompt-card-manage button.is-published.is-outdated:focus-visible",
        )
        self.assertNotIn("199,255,0", hover)

    def test_every_new_label_has_a_chinese_and_an_english_entry(self):
        keys = (
            "library.updatePublished",
            "library.updatePublishedHint",
            "library.publishUpdatedToast",
            "library.updatePublishFailed",
        )
        for key in keys:
            with self.subTest(key=key):
                match = re.search(r'"%s":\s*\{\s*zh:\s*"([^"]+)",\s*en:\s*"([^"]+)"\s*\}' % re.escape(key), LIBRARY_I18N)
                self.assertIsNotNone(match, f"{key} 缺少中英词条")
                self.assertTrue(match.group(1).strip())
                self.assertTrue(match.group(2).strip())


@unittest.skipUnless(NODE, "node is required to render the prompt card template")
class PromptPublicationRenderTests(unittest.TestCase):
    """真实渲染：陈旧时才出现「更新发布」，且只出现在自己的卡片上。"""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [NODE],
            input=NODE_HARNESS,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=120,
        )
        if result.returncode != 0:
            raise AssertionError(f"node failed to render cards: {result.stderr.strip()[:800]}")
        cls.cards = json.loads(result.stdout)

    def test_outdated_card_offers_the_update_action(self):
        html = self.cards["outdated"]
        self.assertIn('class="is-published is-outdated"', html)
        self.assertIn('data-pl-update-publication="p1"', html)
        self.assertIn("更新发布", html)
        self.assertNotIn("data-pl-show-publication", html)
        self.assertIn('title="原提示词已有改动，点击把最新内容同步到公开版本"', html)

    def test_current_card_keeps_the_plain_published_navigation(self):
        html = self.cards["current"]
        self.assertIn('class="is-published"', html)
        self.assertNotIn("is-outdated", html)
        self.assertIn('data-pl-show-publication="published_1"', html)
        self.assertIn("已发布", html)
        self.assertNotIn("data-pl-update-publication", html)

    def test_unpublished_cards_still_offer_publishing(self):
        html = self.cards["unpublished"]
        self.assertIn('data-pl-publish="p3"', html)
        self.assertNotIn("data-pl-update-publication", html)
        self.assertNotIn("is-outdated", html)

    def test_the_flag_alone_never_creates_an_update_button(self):
        # 没有对应公开版本（或不是自己的卡片）时，陈旧标记必须被忽略。
        for key in ("flagWithoutPublication", "otherTab"):
            with self.subTest(card=key):
                self.assertNotIn("data-pl-update-publication", self.cards[key])

    def test_the_source_id_is_escaped(self):
        html = self.cards["escaping"]
        self.assertIn('data-pl-update-publication="&lt;img src=x onerror=1&gt;"', html)
        self.assertNotIn("<img src=x", html)


if __name__ == "__main__":
    unittest.main()
