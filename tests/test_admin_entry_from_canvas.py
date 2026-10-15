from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AdminEntryFromCanvasTests(unittest.TestCase):
    """智能画布账户浮层里的“系统管理”入口，以及管理后台返回原画布的行为。"""

    @classmethod
    def setUpClass(cls):
        cls.canvas_html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.shell_js = (ROOT / "static" / "js" / "smart-canvas-shell.js").read_text(encoding="utf-8")
        cls.shell_css = (ROOT / "static" / "css" / "smart-canvas-shell.css").read_text(encoding="utf-8")
        cls.admin_html = (ROOT / "static" / "admin.html").read_text(encoding="utf-8")

    def test_account_popover_has_hidden_admin_entry(self):
        popover_start = self.canvas_html.index('id="rhAccountPopover"')
        popover_end = self.canvas_html.index("</section>", popover_start)
        popover = self.canvas_html[popover_start:popover_end]
        self.assertIn('id="rhAdminEntry"', popover)
        self.assertIn("系统管理", popover)
        # 默认隐藏，只有管理员会话才显示。
        self.assertIn("hidden", popover[popover.index('id="rhAdminEntry"'):popover.index('id="rhAdminEntry"') + 120])

    def test_entry_visibility_and_navigation_in_shell_js(self):
        account_start = self.shell_js.index("async function loadAccount(){")
        account_end = self.shell_js.index("async function loadBalances", account_start)
        self.assertIn("adminEntry.hidden = user.role !== 'admin'", self.shell_js[account_start:account_end])

        nav_start = self.shell_js.index("document.getElementById('rhAdminEntry')?.addEventListener")
        nav_body = self.shell_js[nav_start:nav_start + 600]
        self.assertIn("await saveCanvas()", nav_body)
        self.assertIn("const back = `${location.pathname}${location.search}`;", nav_body)
        self.assertIn("/static/admin.html?back=${encodeURIComponent(back)}", nav_body)

    def test_admin_page_returns_to_canvas_and_rejects_other_targets(self):
        helper_start = self.admin_html.index("function canvasBackTarget(){")
        helper_end = self.admin_html.index("\n", helper_start)
        helper = self.admin_html[helper_start:helper_end]
        # 只接受回到智能画布的站内相对地址，防止 back 参数被用作开放重定向。
        self.assertIn("startsWith('/static/smart-canvas.html?')", helper)

        return_start = self.admin_html.index("function returnToWorkspace(){")
        return_end = self.admin_html.index("\n", return_start)
        return_body = self.admin_html[return_start:return_end]
        self.assertIn("const back=canvasBackTarget();if(back){window.location.href=back;return}", return_body)
        # 嵌在 Studio 外壳面板里时，通过 postMessage 关闭面板，而不是在 iframe 内跳转 '/'。
        self.assertIn("window.parent.postMessage({type:'studio:close-panel'},location.origin)", return_body)
        self.assertNotIn("switchUI", return_body)
        # 独立打开且没有 back 参数时保持原有“返回工作台”行为。
        self.assertIn("window.location.href='/'", return_body)
        self.assertIn("返回画布", self.admin_html)

    def test_shell_header_hidden_for_admin_panel(self):
        shell_css = (ROOT / "static" / "css" / "studio-shell.css").read_text(encoding="utf-8")
        # 管理台统一使用自己 masthead 的返回入口，外壳的“返回工作台 + 标题”栏必须隐藏。
        self.assertIn('.studio-panel-overlay[data-name="admin"] > header { display:none; }', shell_css)

    def test_admin_entry_style_respects_hidden(self):
        self.assertIn(".rh-account-admin {", self.shell_css)
        # display:flex 会覆盖 hidden 属性，必须显式补一条隐藏规则。
        self.assertIn(".rh-account-admin[hidden] { display:none; }", self.shell_css)


if __name__ == "__main__":
    unittest.main()
