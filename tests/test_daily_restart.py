"""每日 06:00 自动重启：脚本契约 + restart-server.ps1 的 -DryRun 实际行为。

这台机器上的几个硬约束决定了下述断言不是风格问题：
- WMI 会卡死，重启脚本只能用端口反查 PID，禁止 Get-CimInstance / wmic；
- 同机还跑着 ComfyUI 的两个 python，所以必须先校验 exe 路径再 Stop-Process，绝不能按进程名杀；
- 服务必须由根目录「启动服务.bat」在新控制台窗口里拉起，6:00 时分要静默（IC_NO_BROWSER）；
- 计划任务通过 run-hidden.vbs 隐藏窗口调用脚本，每天 06:00。
"""

import re
import socket
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "tools" / "daily-restart"
PS1 = TOOL_DIR / "restart-server.ps1"
INSTALL_PS1 = TOOL_DIR / "install-task.ps1"
UNINSTALL_PS1 = TOOL_DIR / "uninstall-task.ps1"
VBS = TOOL_DIR / "run-hidden.vbs"
LAUNCHER = ROOT / "启动服务.bat"
MANUAL_BAT = ROOT / "每日重启服务.bat"
INSTALL_BAT = ROOT / "安装每日重启任务.bat"
UNINSTALL_BAT = ROOT / "卸载每日重启任务.bat"
TASK_NAME = "InfiniteCanvas_DailyRestart"


def read_text(path):
    return path.read_text(encoding="utf-8-sig")


def code_only(powershell_source):
    """去掉 <# ... #> 帮助块和整行 # 注释，只留会真正执行的代码。"""
    without_block = re.sub(r"<#.*?#>", "", powershell_source, flags=re.S)
    return "\n".join(
        line for line in without_block.splitlines() if not line.lstrip().startswith("#")
    )


def port_listening(port=3000, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


class DailyRestartScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ps1 = read_text(PS1)
        cls.install = read_text(INSTALL_PS1)
        cls.uninstall = read_text(UNINSTALL_PS1)
        cls.vbs = read_text(VBS)
        cls.launcher = read_text(LAUNCHER)
        cls.manual_bat = read_text(MANUAL_BAT)
        cls.install_bat = read_text(INSTALL_BAT)
        cls.uninstall_bat = read_text(UNINSTALL_BAT)

    # ---- 文件与编码 ----

    def test_all_entry_points_exist(self):
        for path in (PS1, INSTALL_PS1, UNINSTALL_PS1, VBS, LAUNCHER, MANUAL_BAT, INSTALL_BAT, UNINSTALL_BAT):
            self.assertTrue(path.is_file(), f"缺少文件: {path}")

    def test_powershell_scripts_are_utf8_with_bom(self):
        # powershell.exe 5.1 无 BOM 时按 ANSI 代码页读脚本，带中文的脚本会被读坏。
        for path in (PS1, INSTALL_PS1, UNINSTALL_PS1):
            self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf", f"{path.name} 缺少 UTF-8 BOM")

    def test_batch_and_vbs_stay_ascii(self):
        # .bat / .vbs 不能带 BOM，也不能让 cmd 去解析多字节注释。
        for path in (LAUNCHER, MANUAL_BAT, INSTALL_BAT, UNINSTALL_BAT, VBS):
            data = path.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), f"{path.name} 不应带 BOM")
            self.assertFalse([b for b in data if b > 127], f"{path.name} 应保持纯 ASCII")

    # ---- 重启脚本行为 ----

    def test_restart_finds_process_by_port_and_never_uses_wmi(self):
        self.assertIn("Get-NetTCPConnection -LocalPort", self.ps1)
        self.assertIn("netstat -ano", self.ps1)  # 回退路径
        for forbidden in ("Get-CimInstance", "Win32_Process", "Get-WmiObject", "wmic "):
            self.assertNotIn(forbidden, code_only(self.ps1), f"重启脚本禁止使用 {forbidden}（本机 WMI 会卡死）")

    def test_restart_verifies_owner_before_killing(self):
        self.assertIn("function Test-ProjectProcess", self.ps1)
        self.assertIn("python\\python.exe", self.ps1)
        self.assertIn("Stop-Process -Id $ProcessId -Force", self.ps1)
        # 拒绝操作必须是非 0 退出码，且不启动第二个实例
        refuse = self.ps1.index("为避免误杀不执行重启")
        self.assertIn("exit 2", self.ps1[refuse:refuse + 120])

    def test_restart_launches_launcher_in_new_window_without_browser(self):
        self.assertIn('$Launcher = Join-Path $BaseDir \'启动服务.bat\'', self.ps1)
        self.assertIn("$env:IC_NO_BROWSER = '1'", self.ps1)
        self.assertIn("Start-Process -FilePath $env:ComSpec", self.ps1)
        self.assertIn("Wait-ServerReady", self.ps1)
        self.assertIn("http://127.0.0.1:", self.ps1)

    def test_launcher_has_window_title_and_browser_guard(self):
        self.assertIn("title Infinite Canvas", self.launcher)
        self.assertIn("if not defined IC_NO_BROWSER", self.launcher)
        # 手动双击仍然会打开浏览器
        browser_line = [line for line in self.launcher.splitlines() if "Start-Process '%APP_URL%'" in line]
        self.assertEqual(len(browser_line), 1)
        self.assertTrue(browser_line[0].lstrip().startswith("if not defined IC_NO_BROWSER"))

    def test_restart_avoids_taskkill_and_tasklist(self):
        # 本机 taskkill（含 /T）与 tasklist /v 都会卡住 60~120 秒，只能走 Stop-Process。
        code = code_only(self.ps1)
        self.assertNotIn("taskkill", code)
        self.assertNotIn("tasklist", code)
        self.assertIn("function Stop-ServerProcess", code)

    def test_restarted_window_closes_itself(self):
        # cmd /c + 受 IC_NO_BROWSER 守卫的 pause：自动重启的窗口在服务停止后自己关闭，不累积黑窗。
        # 必须带 run 参数：启动服务.bat 无参数时会进入交互菜单而不是前台跑服务。
        self.assertIn("@('/c', ('call \"{0}\" run' -f $Launcher))", self.ps1)
        self.assertIn("if not defined IC_NO_BROWSER pause", self.launcher)

    def test_bats_delegate_to_scripts(self):
        self.assertIn("tools\\daily-restart\\restart-server.ps1", self.manual_bat)
        self.assertIn("-NoBrowser", self.manual_bat)
        self.assertIn("tools\\daily-restart\\install-task.ps1", self.install_bat)
        self.assertIn("tools\\daily-restart\\uninstall-task.ps1", self.uninstall_bat)
        for bat in (self.manual_bat, self.install_bat, self.uninstall_bat):
            self.assertIn("chcp 65001", bat)

    # ---- 计划任务 ----

    def test_install_registers_daily_0600_interactive_task(self):
        self.assertIn(f"'{TASK_NAME}'", self.install)
        self.assertIn("'06:00'", self.install)
        self.assertIn("New-ScheduledTaskTrigger -Daily -At", self.install)
        self.assertIn("-StartWhenAvailable", self.install)
        self.assertIn("-LogonType Interactive", self.install)
        self.assertIn("run-hidden.vbs", self.install)

    def test_hidden_wrapper_is_invisible_and_async(self):
        self.assertIn("restart-server.ps1", self.vbs)
        self.assertIn("-NoBrowser", self.vbs)
        self.assertIn("shell.Run cmd, 0, False", self.vbs)

    def test_uninstall_removes_the_task(self):
        self.assertIn(f"'{TASK_NAME}'", self.uninstall)
        self.assertIn("Unregister-ScheduledTask", self.uninstall)

    # ---- 真实 DryRun：不杀进程、能认出当前监听者 ----

    @staticmethod
    def _decode(raw):
        for encoding in ("utf-8", "mbcs", "gbk"):
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", "replace")

    def test_dry_run_identifies_listener_and_changes_nothing(self):
        listening_before = port_listening()
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), "-DryRun"],
            cwd=str(ROOT),
            capture_output=True,
        )
        out = self._decode(proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn(str(ROOT), out)
        self.assertIn("DryRun", out)
        if listening_before:
            self.assertIn("python.exe", out)
            self.assertTrue(port_listening(), "DryRun 不允许结束正在监听 3000 的服务进程")
