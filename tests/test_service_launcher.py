# -*- coding: utf-8 -*-
"""可视化启动器测试：入口规范、git 同步全流程（临时仓库真跑）、安全判定、死代码删除断言。"""
import ast
import io
import os
import shutil
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
        allowed = {"ctypes", "io", "json", "os", "re", "subprocess", "sys",
                   "threading", "time", "urllib", "webbrowser", "http"}
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

    def test_html_has_no_external_assets(self):
        src = read_file(os.path.join(ROOT, "tools", "launcher", "launcher.html"))
        for token in ("//cdn", "unpkg", "jsdelivr", "googleapis"):
            self.assertNotIn(token, src)

    def test_is_project_python_guard(self):
        base = launcher.BASE_DIR
        self.assertTrue(launcher.is_project_python(os.path.join(base, "python", "python.exe")))
        self.assertFalse(launcher.is_project_python(r"C:\Other\python.exe"))
        self.assertFalse(launcher.is_project_python(""))

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
