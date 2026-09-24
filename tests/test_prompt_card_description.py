"""提示词库卡片的统一标准：悬停显示提示词说明。

用户要求（本轮）：灵感库的卡片也要显示提示词说明，有说明就显示说明，没有就显示“无说明”，
即提示词库的所有卡片统一这一标准。

提示词库四个 tab（灵感库 / 我的收藏 / 我的提示词 / 我的发布）共用
``static/js/prompt-library.js`` 的 ``cardHtml()``，所以“统一”意味着说明条不再按 tab 收窄。

覆盖三类断言：
* 静态契约：模板无条件渲染说明条、i18n 词条中英齐全、CSS 两行省略 + 悬停显现 + 触屏常显；
* 行为验证：用 node 在 vm 中加载真实的 prompt-library.js，对四个 tab 各渲染一次卡片，
  断言说明条都存在、空白说明回落到“无说明”、description 缺失时回退到 scene、文本被转义；
* 排版契约：说明条底内边距必须为 0，且悬停层的底部预留高度必须大于说明条自身高度，
  否则（无头 Chrome 实测）会出现“半行文字”残影以及说明条压住“应用 / 预览”按钮。
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT_JS = (ROOT / "static" / "js" / "prompt-library.js").read_text(encoding="utf-8")
PROMPT_CSS = (ROOT / "static" / "css" / "prompt-library.css").read_text(encoding="utf-8")
LIBRARY_I18N = (ROOT / "static" / "js" / "i18n" / "library.js").read_text(encoding="utf-8")

NODE = shutil.which("node")

# 在 vm 里加载真实模块并把 cardHtml() 的产物交回给 Python。script 从 stdin 读取，
# 因此不需要临时文件，cwd 就是仓库根目录。
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

const items = {
    withDescription: { id: 'p1', name: '场景拆分', description: '把一句话拆成镜头级提示词', prefix: 'a', suffix: 'b' },
    withoutDescription: { id: 'p2', name: '无说明项', prefix: 'a', suffix: 'b' },
    blankDescription: { id: 'p3', name: '空白说明项', description: '   ', prefix: 'a', suffix: 'b' },
    sceneOnly: { id: 'p4', name: '仅场景项', scene: '来自 scene 字段的说明', prefix: 'a', suffix: 'b' },
    markupDescription: { id: 'p5', name: '转义项', description: '<img src=x onerror=alert(1)>', prefix: 'a', suffix: 'b' },
};

const out = {};
for (const tab of ['inspiration', 'favorites', 'myPrompts', 'myPublished']) {
    const view = Object.create(PromptLibrary);
    Object.assign(view, {
        tab,
        favorites: new Set(),
        activeTarget: () => null,
        publishedForSource: () => null,
    });
    out[tab] = {};
    for (const [key, item] of Object.entries(items)) out[tab][key] = view.cardHtml(item);
}
process.stdout.write(JSON.stringify(out));
"""


class PromptCardDescriptionContractTests(unittest.TestCase):
    """说明条必须对提示词库的所有卡片生效，而不是只对“我的提示词”生效。"""

    def test_card_template_always_renders_the_description_strip(self):
        self.assertIn(
            "const descriptionNote = `<p class=\"prompt-card-desc${description ? '' : ' is-empty'}\">",
            PROMPT_JS,
        )
        # 说明条不能再按 tab 收窄（历史实现是 `isMine && description`）。
        self.assertNotIn("isMine && description", PROMPT_JS)
        self.assertNotIn("descriptionNote = isMine", PROMPT_JS)
        # 依然渲染在封面容器内部：1 号区域（缩略图底部横带）。
        self.assertIn("</button></div>${descriptionNote}${duplicateButton}</div>", PROMPT_JS)

    def test_missing_description_uses_the_shared_placeholder(self):
        self.assertIn("t('library.noDescription', '无说明')", PROMPT_JS)
        self.assertIn("is-empty", PROMPT_JS)

    def test_description_falls_back_to_the_scene_field(self):
        self.assertIn("String(item.description || item.scene || '').trim()", PROMPT_JS)

    def test_strip_is_clamped_to_two_lines_and_revealed_on_every_card(self):
        self.assertIn(".prompt-card-desc {", PROMPT_CSS)
        self.assertIn("-webkit-line-clamp: 2", PROMPT_CSS)
        self.assertIn("-webkit-box-orient: vertical", PROMPT_CSS)
        self.assertIn("overflow: hidden", PROMPT_CSS)
        # 显现规则挂在 .prompt-card 上（不含 tab 限定），因此四个 tab 一致。
        self.assertIn(
            ".prompt-card:hover .prompt-card-desc, .prompt-card:focus-within .prompt-card-desc { opacity: 1; transform: none; }",
            PROMPT_CSS,
        )
        # 触屏没有 hover，说明条必须常显。
        self.assertIn(
            ".prompt-card-duplicate, .prompt-card-desc { opacity: 1; transform: none; }",
            PROMPT_CSS,
        )
        # 没有说明时用弱化样式区分“无说明”占位。
        self.assertIn(".prompt-card-desc.is-empty {", PROMPT_CSS)

    def test_i18n_has_the_no_description_label(self):
        self.assertIn('"library.noDescription": { zh: "无说明", en: "No description" }', LIBRARY_I18N)


def _rule_body(css_text, selector):
    """取出某条 CSS 规则的声明块（选择器必须精确匹配，不会误命中 `.prompt-card-desc.is-empty`）。"""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css_text)
    if not match:
        raise AssertionError(f"CSS 里找不到规则 {selector}")
    return match.group(1)


def _declaration(body, prop):
    match = re.search(r"(?:^|;)\s*" + re.escape(prop) + r"\s*:\s*([^;]+)", body)
    if not match:
        raise AssertionError(f"规则里找不到声明 {prop}")
    return match.group(1).strip()


def _em_px(expression):
    """把 `calc(2.9em + 7px)` 这类表达式拆成 (em 系数, px 值)。"""
    em = re.search(r"([\d.]+)em", expression)
    px = re.search(r"([\d.]+)px", expression)
    if not em or not px:
        raise AssertionError(f"无法解析高度表达式：{expression}")
    return float(em.group(1)), float(px.group(1))


def _padding_terms(value):
    """按顶层空白切分 padding（`calc(2.9em + 21px)` 内部的空格不算切分点）。"""
    return re.findall(r"calc\([^)]*\)|\S+", value)


class PromptCardDescriptionLayoutTests(unittest.TestCase):
    """排版契约：两行说明条既不能露出“半行”残影，也不能压住封面上的「应用 / 预览」。

    无头 Chrome 实测过两个真实缺陷（用户报障）：
    1. `-webkit-line-clamp: 2` 只把元素高度压到两行，超出的行盒仍在文档流里，
       而 `overflow: hidden` 的裁剪边界是 padding box——底内边距一旦非 0，
       第三行文字的顶部就会从这段内边距里露出来（“半行文字”）。
    2. 两行说明条按 `bottom` 锚定后会向上长高，顶边压住封面居中的按钮。
    """

    DESC = ".prompt-card-desc"
    HOVER = ".prompt-card-hover"
    OFFSET_PX = 7  # 说明条距封面底边的内缩，与 `bottom: 7px` 一致
    GAP_PX = 7  # 说明条顶边与按钮之间必须保留的最小间隙

    @classmethod
    def setUpClass(cls):
        cls.desc = _rule_body(PROMPT_CSS, cls.DESC)
        cls.hover = _rule_body(PROMPT_CSS, cls.HOVER)

    def test_strip_has_no_bottom_padding(self):
        padding = _padding_terms(_declaration(self.desc, "padding"))
        self.assertEqual(len(padding), 3, f"说明条应写成 `padding: 上 左右 下`，实际是 {padding}")
        self.assertEqual(padding[2], "0", "说明条的底部内边距必须为 0，否则会露出被裁掉的半行文字")
        self.assertEqual(_declaration(self.desc, "box-sizing"), "border-box")

    def test_strip_max_height_matches_two_lines(self):
        line_height = float(_declaration(self.desc, "line-height"))
        em, px = _em_px(_declaration(self.desc, "max-height"))
        self.assertAlmostEqual(em, 2 * line_height, places=3, msg="max-height 的 em 部分应恰好等于两行行高")
        self.assertEqual(px, 7, "max-height 的 px 部分 = 5px 上内边距 + 上下各 1px 边框")

    def test_hover_reserve_clears_the_strip(self):
        padding = _padding_terms(_declaration(self.hover, "padding"))
        self.assertEqual(len(padding), 3, f"悬停层应写成 `padding: 上 左右 下`，实际是 {padding}")
        reserve_em, reserve_px = _em_px(padding[2])
        strip_em, strip_px = _em_px(_declaration(self.desc, "max-height"))
        self.assertAlmostEqual(reserve_em, strip_em, places=3, msg="悬停预留与说明条必须用同一套 em 高度")
        self.assertEqual(
            reserve_px - strip_px,
            self.OFFSET_PX + self.GAP_PX,
            "悬停层的底部预留 = 说明条高度 + 说明条底边内缩 + 按钮间隙，否则按钮会与说明条重叠",
        )

    def test_hover_font_size_matches_the_strip(self):
        self.assertEqual(
            _declaration(self.hover, "font-size"),
            _declaration(self.desc, "font-size"),
            "悬停层与说明条的字号必须同源，否则 em 预留高度对不上",
        )


@unittest.skipUnless(NODE, "node is required to render the prompt card template")
class PromptCardDescriptionRenderTests(unittest.TestCase):
    """真实渲染：四个 tab 的卡片都必须带说明条，且“无说明”回落正确。"""

    TABS = ("inspiration", "favorites", "myPrompts", "myPublished")

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

    def strip(self, tab, key):
        html = self.cards[tab][key]
        start = html.find('<p class="prompt-card-desc')
        self.assertNotEqual(start, -1, f"{tab}/{key} 没有渲染说明条：{html[:200]}")
        end = html.find("</p>", start)
        block = html[start:end]
        return block

    def test_all_four_tabs_render_the_description_strip(self):
        for tab in self.TABS:
            with self.subTest(tab=tab):
                block = self.strip(tab, "withDescription")
                self.assertIn("把一句话拆成镜头级提示词", block)
                self.assertNotIn("is-empty", block)

    def test_all_four_tabs_fall_back_to_the_placeholder(self):
        for tab in self.TABS:
            with self.subTest(tab=tab):
                for key in ("withoutDescription", "blankDescription"):
                    block = self.strip(tab, key)
                    self.assertIn("is-empty", block)
                    self.assertIn("无说明", block)

    def test_scene_is_used_when_description_is_absent(self):
        for tab in self.TABS:
            with self.subTest(tab=tab):
                block = self.strip(tab, "sceneOnly")
                self.assertIn("来自 scene 字段的说明", block)
                self.assertNotIn("无说明", block)

    def test_description_text_is_escaped(self):
        for tab in self.TABS:
            with self.subTest(tab=tab):
                html = self.cards[tab]["markupDescription"]
                self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
                self.assertNotIn("<img src=x", html)

    def test_duplicate_button_stays_limited_to_my_prompts(self):
        for tab in self.TABS:
            with self.subTest(tab=tab):
                has_button = "data-pl-duplicate" in self.cards[tab]["withDescription"]
                self.assertEqual(has_button, tab == "myPrompts")


if __name__ == "__main__":
    unittest.main()
