# -*- coding: utf-8 -*-
"""可视化启动器测试：入口规范、git 同步全流程（临时仓库真跑）、安全判定、死代码删除断言。"""
import ast
import io
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from importlib import import_module


def force_rmtree(path):
    """git 对象文件只读，普通 rmtree 在 Windows 会静默残留；去只读属性后重试。"""
    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "launcher"))
launcher = import_module("launcher")


def sh(args, cwd):
    proc = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(f"命令失败 {args}: {proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout.decode("utf-8", "replace")


def git(args, cwd):
    return sh(["git", "-c", "user.name=T", "-c", "user.email=t@local",
               "-c", "core.autocrlf=false"] + args, cwd)


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_file(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _code_without_comments_and_strings(src):
    """只留真正的可执行代码：注释、docstring、普通字符串常量全部剔除。

    注释里写"① --app=URL 会被提权拦截"是有价值的踩坑记录，不应该被断言误伤；
    但真正调用 --app= 的代码必须一个都不剩。
    """
    tree = ast.parse(src)
    drop = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", []) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                drop.add(id(body[0].value))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            drop.add(id(node))
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            chunks.append(node.id)
        elif isinstance(node, ast.Attribute):
            chunks.append(node.attr)
        elif isinstance(node, ast.Constant) and id(node) not in drop:
            chunks.append(str(node.value))
    return "\n".join(chunks)


def snapshot_tree(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "logs")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                out[rel] = read_file(full)
            except Exception:  # noqa: BLE001
                out[rel] = "<binary>"
    return out


class RepoCase(unittest.TestCase):
    """临时 bare origin + 两个工作副本，模拟"别的机器推送、本机拉取"。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="launcher-git-")
        self.addCleanup(force_rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "--bare", "-b", "main", self.origin],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.work1 = os.path.join(self.tmp, "work1")
        subprocess.run(["git", "clone", self.origin, self.work1],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        git(["config", "core.autocrlf", "false"], self.work1)
        write_file(os.path.join(self.work1, "f1.txt"), "line-one v1\n")
        write_file(os.path.join(self.work1, "f2.txt"), "other file\n")
        git(["add", "-A"], self.work1)
        git(["commit", "-m", "A"], self.work1)
        git(["push", "-u", "origin", "main"], self.work1)
        self.work2 = os.path.join(self.tmp, "work2")
        git(["clone", self.origin, self.work2], self.tmp)
        git(["config", "core.autocrlf", "false"], self.work2)

    def tearDown(self):
        force_rmtree(self.tmp)

    def remote_push(self, message):
        git(["add", "-A"], self.work2)
        git(["commit", "-m", message], self.work2)
        git(["push", "origin", "main"], self.work2)

    def sync1(self):
        return launcher.GitSync(self.work1)

    # ------------------------------------------------------------ 基本同步

    def test_fast_forward_update(self):
        write_file(os.path.join(self.work2, "f1.txt"), "line-one v2\n")
        self.remote_push("remote v2")
        gs = self.sync1()
        gs.fetch()
        state = gs.state()
        self.assertEqual(state["behind"], 1)
        self.assertEqual(len(state["behind_commits"]), 1)
        result = gs.update()
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(read_file(os.path.join(self.work1, "f1.txt")), "line-one v2\n")
        self.assertEqual(gs.state()["behind"], 0)

    def test_push_backups_local_commits(self):
        write_file(os.path.join(self.work1, "f2.txt"), "local edit\n")
        git(["add", "-A"], self.work1)
        git(["commit", "-m", "local"], self.work1)
        result = self.sync1().push()
        self.assertTrue(result["ok"], result["message"])
        head = git(["rev-parse", "main"], self.origin).strip()
        self.assertEqual(head, git(["rev-parse", "HEAD"], self.work1).strip())

    # ------------------------------------------------------------ 冲突处理

    def test_diverged_conflict_resolve_ours_and_theirs(self):
        write_file(os.path.join(self.work1, "f1.txt"), "line-one MINE\n")
        git(["add", "-A"], self.work1)
        git(["commit", "-m", "mine"], self.work1)
        write_file(os.path.join(self.work2, "f1.txt"), "line-one THEIRS\n")
        write_file(os.path.join(self.work2, "f2.txt"), "f2 THEIRS\n")
        self.remote_push("theirs")
        gs = self.sync1()
        gs.fetch()
        result = gs.update()
        self.assertTrue(result.get("conflicts"), result["message"])
        # update 这次没有先跑一次 state()，缓存未被污染；冲突后必须立刻看到 f1 是冲突态
        self.assertIn("f1.txt", gs.state()["unmerged"])
        # 再把缓存预热一次：动作方法收尾必须丢弃旧缓存，不能拿冲突前的快照糊弄页面
        gs.update()
        self.assertIn("f1.txt", gs.state()["unmerged"])
        # f1 保留我的；批量 f2 用新版
        resolved = gs.resolve("f1.txt", "ours")
        self.assertTrue(resolved["ok"], resolved["message"])
        left = gs.state()["unmerged"]
        if left:
            gs.resolve("__all__", "theirs")
        self.assertEqual(gs.state()["unmerged"], [])
        self.assertEqual(read_file(os.path.join(self.work1, "f1.txt")), "line-one MINE\n")
        self.assertEqual(read_file(os.path.join(self.work1, "f2.txt")), "f2 THEIRS\n")

    def test_undo_restores_tree_byte_identical(self):
        write_file(os.path.join(self.work1, "f1.txt"), "line-one MINE\n")
        git(["add", "-A"], self.work1)
        git(["commit", "-m", "mine"], self.work1)
        write_file(os.path.join(self.work2, "f1.txt"), "line-one THEIRS\n")
        self.remote_push("theirs")
        before = snapshot_tree(self.work1)
        head_before = git(["rev-parse", "HEAD"], self.work1).strip()
        gs = self.sync1()
        gs.fetch()
        self.assertTrue(gs.update().get("conflicts"))
        result = gs.undo()
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(snapshot_tree(self.work1), before)
        self.assertEqual(git(["rev-parse", "HEAD"], self.work1).strip(), head_before)

    # ------------------------------------------------------------ 未提交改动

    def test_dirty_roundtrip_no_conflict(self):
        write_file(os.path.join(self.work1, "f2.txt"), "my uncommitted\n")  # 不提交
        write_file(os.path.join(self.work2, "f1.txt"), "line-one v2\n")
        self.remote_push("remote v2")
        gs = self.sync1()
        gs.fetch()
        result = gs.update()
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(read_file(os.path.join(self.work1, "f1.txt")), "line-one v2\n")
        self.assertEqual(read_file(os.path.join(self.work1, "f2.txt")), "my uncommitted\n")
        self.assertIn("f2.txt", gs.state()["dirty"])
        self.assertEqual(git(["stash", "list"], self.work1).strip(), "")

    def test_stash_conflict_resolve_mine(self):
        write_file(os.path.join(self.work1, "f1.txt"), "line-one MYDIRTY\n")  # 未提交
        write_file(os.path.join(self.work2, "f1.txt"), "line-one REMOTECOMMIT\n")
        self.remote_push("remote same line")
        gs = self.sync1()
        gs.fetch()
        result = gs.update()
        self.assertTrue(result.get("conflicts"), result["message"])
        self.assertEqual(gs.update_record()["phase"], "stash_conflict")
        done = gs.resolve("f1.txt", "ours")  # ours=保留我的（未提交改动）
        self.assertTrue(done["ok"], done["message"])
        self.assertEqual(read_file(os.path.join(self.work1, "f1.txt")), "line-one MYDIRTY\n")
        self.assertEqual(git(["stash", "list"], self.work1).strip(), "")

    def test_stash_conflict_undo_restores_dirty_on_old_base(self):
        write_file(os.path.join(self.work1, "f1.txt"), "line-one MYDIRTY\n")
        write_file(os.path.join(self.work2, "f1.txt"), "line-one REMOTECOMMIT\n")
        self.remote_push("remote same line")
        head_before = git(["rev-parse", "HEAD"], self.work1).strip()
        gs = self.sync1()
        gs.fetch()
        self.assertTrue(gs.update().get("conflicts"))
        result = gs.undo()
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(read_file(os.path.join(self.work1, "f1.txt")), "line-one MYDIRTY\n")
        self.assertEqual(git(["rev-parse", "HEAD"], self.work1).strip(), head_before)
        self.assertEqual(git(["stash", "list"], self.work1).strip(), "")


class StaticSpecCase(unittest.TestCase):

    def test_bat_is_ascii_and_delegates(self):
        with open(os.path.join(ROOT, "启动器.bat"), "rb") as f:
            raw = f.read()
        self.assertEqual(raw.decode("ascii", errors="strict").encode("ascii"), raw, "启动器.bat 必须纯 ASCII")
        text = raw.decode("ascii")
        self.assertIn("tools\\launcher\\launcher.py", text)
        self.assertNotIn("Stop-Process", text)

    def test_launcher_py_stdlib_only(self):
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        tree = ast.parse(src)
        allowed = {"ctypes", "io", "json", "os", "re", "socket", "subprocess", "sys",
                   "threading", "time", "urllib", "uuid", "http"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.assertIn(node.module.split(".")[0], allowed, node.module)

    def test_launcher_binds_loopback_only(self):
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        self.assertIn('ThreadingHTTPServer(("127.0.0.1", port), Handler)', src)

    def test_window_is_spawned_not_browser(self):
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        # 双击后"没有任何窗口"必须可见：日志 + 弹窗兜底 + 自己拉起原生窗口
        self.assertIn("def log_line", src)
        self.assertIn("launcher.log", src)
        self.assertIn("def spawn_window", src)
        self.assertIn('"window.py"', src)
        self.assertIn("MessageBoxW", src)
        self.assertIn("threading.Thread(target=spawn_window", src)
        # 浏览器路线在本机实测不可用（--app 被提权拦截、webbrowser 打开已卸载的 msedge），
        # 必须保持彻底移除，不能回退。注释与字符串里的历史教训说明不算实现。
        body = _code_without_comments_and_strings(src)
        for token in ("import webbrowser", "find_chrome", "find_edge", "open_window_with_fallback", "--app="):
            self.assertNotIn(token, body)
        # 主线程必须先 serve_forever，不能先阻塞等窗口
        self.assertNotIn("\n    spawn_window(url)\n", src)

    def test_window_host_has_no_console_and_remembers_geometry(self):
        src = read_file(os.path.join(ROOT, "tools", "launcher", "window.py"))
        self.assertIn("def run_window", src)
        self.assertIn("launcher_window.json", src)
        self.assertIn("Microsoft.Web.WebView2.WinForms", src)
        self.assertIn("CREATE_NO_WINDOW", read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py")))
        # WebView2 内核只能在 UI 线程取；绝不能直接 Wait 初始化任务（会死等）
        self.assertNotIn(".Wait(15000)", src)
        self.assertNotIn("core.Navigate", read_file(os.path.join(ROOT, "tools", "launcher", "window.py")).split("def _watch_webview_task")[0])

    def test_webview2_components_bundled(self):
        folder = os.path.join(ROOT, "tools", "launcher", "webview2")
        for name in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll",
                     "WebView2Loader.dll"):
            path = os.path.join(folder, name)
            self.assertTrue(os.path.exists(path), name)
            self.assertGreater(os.path.getsize(path), 1000, name)

    def test_live_launcher_single_instance_probe(self):
        # 端口文件不存在/内容坏掉时必须安全返回 0，不能抛异常
        self.assertEqual(launcher.live_launcher.__name__, "live_launcher")
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        self.assertIn("def live_launcher", src)
        self.assertIn("只重新打开窗口", src)
        self.assertIn("/api/ping", src)

    def test_close_window_keeps_launcher_and_service(self):
        # 用户明确要求：关窗后服务继续运行，再双击只重开窗口，不能被看门狗自动杀掉
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        watchdog = src.split("def watchdog(")[1].split("\ndef ")[0]
        self.assertIn("not HEARTBEAT[\"loaded\"]", watchdog)
        self.assertNotIn("HEARTBEAT[\"last\"]", watchdog)

    def test_quit_buttons_have_distinct_semantics(self):
        # 关窗=只关窗口；「退出启动器」=连服务一起关；「打开工作台」交给系统浏览器
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        self.assertIn('path == "/api/quit"', src)
        self.assertIn("def quit_launcher", src)
        self.assertIn("def open_external", src)
        self.assertIn("127", src.split("def open_external(")[1].split("\ndef ")[0])
        html = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.html"))
        self.assertIn("/api/quit", html)
        self.assertIn("/api/open-url", html)
        self.assertIn("openWorkbench", html)
        window = read_file(os.path.join(ROOT, "tools", "launcher", "window.py"))
        self.assertIn("os.startfile", window.split("def _on_new_window(")[1].split("\n    def ")[0])

    def test_stop_and_restart_never_touch_foreign_service(self):
        # 事故回归：用户自己双击 bat 起的服务不能被启动器的停止/重启/退出杀掉
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.py"))
        stop_own = src.split("def stop_own_service(")[1].split("\ndef ")[0]
        self.assertIn("SESSION_STATE", stop_own)
        self.assertIn("return None", stop_own)
        stop = src.split("def stop_service(")[1].split("\ndef ")[0]
        self.assertIn("stop_own_service()", stop)
        self.assertNotIn("terminate_pid", stop)
        restart = src.split("def restart_service(")[1].split("\n# ---")[0]
        self.assertIn("stop_own_service()", restart)
        self.assertNotIn("terminate_pid", restart)
        self.assertIn("if stopped is None:", restart)
        quit_block = src.split("def quit_launcher(")[1].split("\ndef ")[0]
        self.assertIn("stop_own_service()", quit_block)
        self.assertNotIn("stop_service()", quit_block)
        start_block = src.split("def start_service(")[1].split("\ndef ")[0]
        self.assertIn("RUN_ID_ENV", start_block)
        self.assertIn("SESSION_STATE", start_block)
        status = src.split("def service_status(")[1].split("\ndef ")[0]
        self.assertIn('"managed"', status)
        html = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.html"))
        self.assertIn("!s.managed", html)
        self.assertNotIn("!s.ours", html)

    def test_window_geometry_survives_bom_and_logs_restore(self):
        # 位置记忆三连坑回归：BOM 读不了、屏幕判断依赖 .NET 点对象、拿不到屏幕就丢弃位置
        window = read_file(os.path.join(ROOT, "tools", "launcher", "window.py"))
        self.assertIn('encoding="utf-8-sig"', window)
        rects = window.split("def _screen_rects(")[1].split("\ndef ")[0]
        self.assertIn("import clr", rects)
        self.assertIn("AllScreens", rects)
        self.assertNotIn("Point(", rects)
        on_screen = window.split("def _is_on_screen(")[1].split("\ndef ")[0]
        self.assertIn("return True", on_screen)
        remember = window.split("def _remember_geometry(")[1].split("\n    def ")[0]
        self.assertIn("Minimized", remember)
        self.assertIn("_saved_box", remember)
        self.assertIn("_is_on_screen", remember)

    def test_html_has_no_external_assets(self):
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.html"))
        for token in ("//cdn", "unpkg", "jsdelivr", "googleapis"):
            self.assertNotIn(token, src)

    def test_is_project_python_guard(self):
        base = launcher.BASE_DIR
        self.assertTrue(launcher.is_project_python(os.path.join(base, "python", "python.exe")))
        self.assertFalse(launcher.is_project_python(r"C:\Other\python.exe"))
        self.assertFalse(launcher.is_project_python(""))

    def test_listening_pid_matches_loopback_only_listener(self):
        # 回归：服务只监听 127.0.0.1 时也必须被认出来（旧实现只认 0.0.0.0 对端）
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(probe.close)
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        port = probe.getsockname()[1]
        self.assertEqual(launcher.listening_pid(port), os.getpid())

    def test_owns_pid_requires_session_record(self):
        # 空闲时端口没有服务、也没有会话记录：任何 pid 都不能被认作自己的
        self.assertFalse(launcher.owns_pid(None))
        self.assertFalse(launcher.owns_pid(0))
        if not os.path.exists(launcher.SESSION_STATE):
            self.assertFalse(launcher.owns_pid(os.getpid()))

    def test_service_status_exposes_occupant_pid(self):
        status = launcher.service_status()
        self.assertIn("occupant_pid", status)
        self.assertEqual(status["pid"], status["occupant_pid"])
        html = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.html"))
        self.assertIn("occupant_pid", html)

    def test_main_py_update_dead_code_removed(self):
        src = read_file(os.path.join(ROOT, "main.py"))
        for token in ("/api/update-from-github", "/api/update-rollback", "/api/update-backups",
                      "/api/update-connectivity", "/api/check-update", "def connectivity_probe",
                      "def safe_update_target", "def schedule_self_restart", "UPDATE_LOCK",
                      "MODELSCOPE_TREE_URL", "GITHUB_VERSION_URL"):
            self.assertNotIn(token, src, token)
        self.assertIn('@app.get("/api/app-info")', src)
        self.assertIn('"canvas_static_version": canvas_static_version()', src)
        self.assertNotIn('"sources": {', src[src.index('@app.get("/api/app-info")'):src.index('@app.get("/api/app-info")') + 600])

    def test_studio_shell_update_ui_removed(self):
        src = read_file(os.path.join(ROOT, "static", "js", "studio-shell.js"))
        self.assertNotIn("check-update", src)
        self.assertNotIn("checkUpdate", src)
        self.assertIn("Digital-Dabbler/Infinite-Canvas", src)


if __name__ == "__main__":
    unittest.main()
