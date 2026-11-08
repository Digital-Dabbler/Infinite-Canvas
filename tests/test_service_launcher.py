"""启动器契约：启动服务.bat 薄壳 + tools/launcher/launcher.ps1 菜单 + restart-server.ps1 -Action。

背景：「启动服务.bat」从"双击即前台跑服务"改成了启动器——
- 无参数且无 IC_NO_BROWSER 时调用 tools\\launcher\\launcher.ps1 显示中文菜单；
- 前台跑服务的旧路径保留在 :run 段（参数 run 或 IC_NO_BROWSER 定义时进入）；
- 菜单的启动/重启/停止全部委托 restart-server.ps1 的 -Action Start/Restart/Stop，
  默认值 Restart 保证每日 06:00 计划任务与「每日重启服务.bat」行为不变；
- main.py 的在线更新自重启（schedule_self_restart）必须带 run 参数，否则会拉起一个菜单窗口而不是服务。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "启动服务.bat"
LAUNCHER_PS1 = ROOT / "tools" / "launcher" / "launcher.ps1"
RESTART_PS1 = ROOT / "tools" / "daily-restart" / "restart-server.ps1"
MAIN_PY = ROOT / "main.py"


def code_only(powershell_source):
    """去掉 <# ... #> 帮助块和整行 # 注释，只留会真正执行的代码。"""
    without_block = re.sub(r"<#.*?#>", "", powershell_source, flags=re.S)
    return "\n".join(
        line for line in without_block.splitlines() if not line.lstrip().startswith("#")
    )


class ServiceLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bat = LAUNCHER.read_text(encoding="utf-8")
        cls.menu_ps1 = LAUNCHER_PS1.read_text(encoding="utf-8-sig")
        cls.restart_ps1 = RESTART_PS1.read_text(encoding="utf-8-sig")
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")

    # ---- bat 薄壳 ----

    def test_bat_stays_pure_ascii_without_bom(self):
        data = LAUNCHER.read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertFalse([b for b in data if b > 127], "启动服务.bat 必须保持纯 ASCII（cmd 解析多字节有坑）")

    def test_bat_routes_to_run_mode_or_menu(self):
        self.assertIn('if /i "%~1"=="run" goto :run', self.bat)
        self.assertIn("if defined IC_NO_BROWSER goto :run", self.bat)
        self.assertIn(":run", self.bat)
        self.assertIn(r'tools\launcher\launcher.ps1', self.bat)
        # 前台模式仍然自己跑服务器并保留旧守卫
        self.assertIn('"%PYEXE%" main.py', self.bat)

    # ---- 菜单脚本 ----

    def test_menu_ps1_is_utf8_with_bom(self):
        # powershell.exe 5.1 无 BOM 时按 ANSI 代码页读脚本，带中文的脚本会被读坏。
        self.assertEqual(LAUNCHER_PS1.read_bytes()[:3], b"\xef\xbb\xbf", "launcher.ps1 缺少 UTF-8 BOM")

    def test_menu_delegates_all_actions_to_restart_script(self):
        self.assertIn("restart-server.ps1", self.menu_ps1)
        self.assertIn("Invoke-RestartAction -Action Start", self.menu_ps1)
        self.assertIn("Invoke-RestartAction -Action Restart -NoBrowser", self.menu_ps1)
        self.assertIn("Invoke-RestartAction -Action Stop", self.menu_ps1)
        self.assertIn("$psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $RestartPs1, '-Action', $Action, '-Port', $Port)", self.menu_ps1)
        self.assertIn("'-NoBrowser'", self.menu_ps1)

    def test_menu_never_kills_processes_itself(self):
        # 杀进程逻辑只允许存在于 restart-server.ps1；菜单只做只读状态展示。
        # 只看真实代码：帮助块里"禁止 taskkill / tasklist / WMI"的说明文字不算违规。
        code = code_only(self.menu_ps1)
        for forbidden in ("Stop-Process", "taskkill", "tasklist", "Get-CimInstance", "Get-WmiObject", "wmic "):
            self.assertNotIn(forbidden, code, f"菜单脚本禁止 {forbidden}")

    def test_menu_shows_status_and_urls(self):
        self.assertIn("Get-ListenPid", self.menu_ps1)
        self.assertIn("Test-ProjectProcess", self.menu_ps1)
        self.assertIn("127.0.0.1", self.menu_ps1)
        for item in ("启动服务", "重启服务", "停止服务", "打开网页", "退出启动器"):
            self.assertIn(item, self.menu_ps1)

    # ---- restart-server.ps1 -Action ----

    def test_restart_script_has_action_param_defaulting_to_restart(self):
        self.assertIn("[ValidateSet('Start', 'Restart', 'Stop')]", self.restart_ps1)
        self.assertIn("[string] $Action               = 'Restart',", self.restart_ps1)

    def test_restart_script_action_branches(self):
        self.assertIn("switch ($Action) {", self.restart_ps1)
        for branch in ("'Stop' {", "'Start' {", "'Restart' {"):
            self.assertIn(branch, self.restart_ps1)
        # Start 幂等：已在运行不重复拉起；Stop 只杀不启
        self.assertIn("无需重复启动", self.restart_ps1)
        self.assertIn("无需停止", self.restart_ps1)

    def test_restart_script_launches_launcher_in_run_mode(self):
        self.assertIn("""@('/c', ('call "{0}" run' -f $Launcher))""", self.restart_ps1)

    # ---- main.py 自重启契约 ----

    def test_self_restart_passes_run_argument(self):
        self.assertIn('cmd /k call \\"%LAUNCHER%\\" run', self.main_py)


if __name__ == "__main__":
    unittest.main()
