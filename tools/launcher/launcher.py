# -*- coding: utf-8 -*-
"""无限画布 · 可视化启动器控制台（仅监听 127.0.0.1，标准库实现）。

职责：服务启停/重启、日志滚动、git 与 origin（用户自己的 fork）同步、
冲突逐文件/批量选边处理、更新备份点与撤销。
"""
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(BASE_DIR, "logs")
SERVER_LOG = os.path.join(LOG_DIR, "launcher_server.log")
SERVICE_STATE = os.path.join(LOG_DIR, "launcher_server.json")
PORT_FILE = os.path.join(LOG_DIR, "launcher_port.txt")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.html")

SERVICE_PORT = 3000
PREFERRED_PORT = 3100
GIT_IDENTITY = ["-c", "user.name=InfiniteCanvasLauncher", "-c", "user.email=launcher@local"]

# ---------------------------------------------------------------- 工具函数

def run_cmd(args, cwd=BASE_DIR, timeout=60):
    try:
        proc = subprocess.run(
            args, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return {
            "code": proc.returncode,
            "out": proc.stdout.decode("utf-8", errors="replace"),
            "err": proc.stderr.decode("utf-8", errors="replace"),
        }
    except FileNotFoundError as exc:
        return {"code": -1, "out": "", "err": f"命令不存在：{exc}"}
    except subprocess.TimeoutExpired:
        return {"code": -1, "out": "", "err": f"命令超时（{timeout}s）：{args}"}
    except Exception as exc:  # noqa: BLE001
        return {"code": -1, "out": "", "err": str(exc)}


def read_json(path, default):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def write_json(path, payload):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass


def project_python():
    exe = os.path.join(BASE_DIR, "python", "python.exe")
    return exe if os.path.exists(exe) else sys.executable


def is_project_python(exe_path):
    """安全判定：只有本项目 python\\ 目录（或项目目录内）的解释器才允许被停止。"""
    if not exe_path:
        return False
    norm = os.path.normpath(exe_path).lower()
    return norm.startswith(os.path.normpath(os.path.join(BASE_DIR, "python")).lower() + os.sep) \
        or norm.startswith(os.path.normpath(BASE_DIR).lower() + os.sep)


def _kernel32():
    return ctypes.windll.kernel32 if os.name == "nt" else None


def pid_image_path(pid):
    if os.name != "nt" or not pid:
        return ""
    try:
        import ctypes.wintypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = ctypes.wintypes.DWORD(4096)
            if ctypes.windll.advapi32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""


def listening_pid(port=SERVICE_PORT):
    """netstat 找监听端口进程（本机 WMI/tasklist 会卡死，必须避开）。"""
    res = run_cmd(["netstat", "-ano", "-p", "tcp"], timeout=20)
    if res["code"] != 0:
        return None
    suffix = f":{port}"
    for line in res["out"].splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper() == "TCP" and parts[1].endswith(suffix) \
                and parts[2] == "0.0.0.0:0" and parts[3].upper() == "LISTENING":
            try:
                return int(parts[-1])
            except ValueError:
                continue
    return None


def terminate_pid(pid):
    k32 = _kernel32()
    if k32 is None:
        return False
    PROCESS_TERMINATE = 0x0001
    handle = k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False
    try:
        return bool(k32.TerminateProcess(handle, 1))
    finally:
        k32.CloseHandle(handle)


def wait_port_free(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if listening_pid(port) is None:
            return True
        time.sleep(0.3)
    return listening_pid(port) is None


_LAN_CACHE = {"ip": "", "at": 0.0}


def lan_address():
    """与 启动服务.bat 同思路：PowerShell 只读探测本机局域网 IPv4（缓存 60s，避免每 2s 起进程）。"""
    if time.time() - _LAN_CACHE["at"] < 60:
        return _LAN_CACHE["ip"]
    _LAN_CACHE["at"] = time.time()
    _LAN_CACHE["ip"] = ""
    try:
        res = run_cmd([
            "powershell", "-NoProfile", "-Command",
            "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1 -ExpandProperty IPAddress)",
        ], timeout=15)
        ip = res["out"].strip().splitlines()
        if res["code"] == 0 and ip and re.match(r"^\d+\.\d+\.\d+\.\d+$", ip[0]):
            _LAN_CACHE["ip"] = ip[0]
    except Exception:  # noqa: BLE001
        pass
    return _LAN_CACHE["ip"]


# ---------------------------------------------------------------- 服务管理

def service_status():
    pid = listening_pid()
    state = read_json(SERVICE_STATE, {})
    running = pid is not None
    ours = bool(pid and is_project_python(pid_image_path(pid)))
    external = running and not ours
    return {
        "running": running,
        "ours": ours,
        "external": external,
        "pid": pid or state.get("pid") or 0,
        "port": SERVICE_PORT,
        "url": f"http://127.0.0.1:{SERVICE_PORT}",
        "lan": lan_address() if running else "",
        "started_at": state.get("started_at", "") if state.get("pid") == pid else "",
    }


def start_service():
    current = service_status()
    if current["running"]:
        return {"ok": True, "message": "服务已在运行", "state": current}
    os.makedirs(LOG_DIR, exist_ok=True)
    exe = project_python()
    if not os.path.exists(exe):
        return {"ok": False, "message": "找不到可用的 Python 解释器", "state": current}
    logf = io.open(SERVER_LOG, "ab")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED | CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            [exe, "main.py"], cwd=BASE_DIR,
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as exc:  # noqa: BLE001
        logf.close()
        return {"ok": False, "message": f"启动失败：{exc}", "state": current}
    write_json(SERVICE_STATE, {"pid": proc.pid, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "exe": exe})
    ready = wait_port_ready(SERVICE_PORT, timeout=60)
    return {
        "ok": ready,
        "message": "服务已就绪" if ready else "进程已启动，但 60 秒内端口未就绪，请查看日志",
        "state": service_status(),
    }


def wait_port_ready(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = listening_pid(port)
        if pid and is_project_python(pid_image_path(pid)):
            return True
        time.sleep(0.5)
    return False


def stop_service():
    pid = listening_pid()
    if pid is None:
        return {"ok": True, "message": "服务本来就没有在运行", "state": service_status()}
    exe = pid_image_path(pid)
    if not is_project_python(exe):
        return {"ok": False, "message": f"端口 {SERVICE_PORT} 被外部程序占用（{exe or '未知'}），为避免误杀不执行停止", "state": service_status()}
    terminate_pid(pid)
    freed = wait_port_free(SERVICE_PORT)
    return {"ok": freed, "message": "服务已停止" if freed else "已发送停止，但端口仍被占用，请查看任务管理器", "state": service_status()}


def restart_service():
    stopped = stop_service()
    if not stopped["ok"]:
        return stopped
    return start_service()

# ---------------------------------------------------------------- git 同步

class GitSync:
    def __init__(self, cwd=BASE_DIR):
        self.cwd = cwd
        self.record_path = os.path.join(cwd, "logs", "launcher_update.json")

    def _record(self):
        return read_json(self.record_path, {})

    def _save_record(self, record):
        path = self.record_path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def git(self, args, timeout=60):
        return run_cmd(["git"] + args, cwd=self.cwd, timeout=timeout)

    def head(self):
        return self.git(["rev-parse", "HEAD"])["out"].strip()

    def branch(self):
        return self.git(["rev-parse", "--abbrev-ref", "HEAD"])["out"].strip()

    def tracked_dirty(self):
        res = self.git(["status", "--porcelain", "--untracked-files=no"])
        return [line[3:] for line in res["out"].splitlines() if line.strip()]

    def unmerged(self):
        res = self.git(["diff", "--name-only", "--diff-filter=U"])
        return [line for line in res["out"].splitlines() if line.strip()]

    def state(self):
        branch = self.branch()
        origin = self.git(["remote", "get-url", "origin"])["out"].strip()
        info = {
            "branch": branch,
            "origin": origin,
            "head": self.head(),
            "version": "",
            "ahead": None,
            "behind": None,
            "behind_commits": [],
            "dirty": self.tracked_dirty(),
            "unmerged": self.unmerged(),
            "error": "",
        }
        try:
            with io.open(os.path.join(self.cwd, "VERSION"), "r", encoding="utf-8") as f:
                info["version"] = (f.read().strip().splitlines() or [""])[0].strip()
        except Exception:  # noqa: BLE001
            pass
        if not origin:
            info["error"] = "本机仓库没有 origin 远端，无法同步"
            return info
        remote_ref = f"origin/{branch}"
        if self.git(["rev-parse", "--verify", remote_ref])["code"] != 0:
            info["error"] = f"尚未拉取过远端分支 {remote_ref}，请先点【检查更新】"
            return info
        info["behind"] = int(self.git(["rev-list", "--count", f"HEAD..{remote_ref}"])["out"].strip() or 0)
        info["ahead"] = int(self.git(["rev-list", "--count", f"{remote_ref}..HEAD"])["out"].strip() or 0)
        log = self.git(["log", "--oneline", f"HEAD..{remote_ref}", "--max-count", "50"])["out"]
        for line in log.splitlines():
            if line.strip():
                sha, _, subject = line.strip().partition(" ")
                info["behind_commits"].append({"sha": sha, "subject": subject})
        return info

    def fetch(self):
        res = self.git(["fetch", "origin", "--prune"], timeout=120)
        if res["code"] != 0:
            return {"ok": False, "message": f"fetch 失败：{(res['err'] or res['out']).strip()[:300]}", "state": self.state()}
        return {"ok": True, "message": "已获取远端最新状态", "state": self.state()}

    def push(self):
        branch = self.branch()
        res = self.git(["push", "origin", f"HEAD:{branch}"], timeout=180)
        ok = res["code"] == 0
        note = "" if not self.tracked_dirty() else "（注意：未提交的改动不在推送范围内）"
        return {"ok": ok, "message": ("已推送 " if ok else "推送失败：") + (branch if ok else (res["err"] or res["out"]).strip()[:300]) + note,
                "state": self.state()}

    def update(self):
        """一键更新：脏改动 stash → 记录备份点 → ff-only/merge origin/分支 → stash pop。"""
        state = self.state()
        if state["error"]:
            return {"ok": False, "message": state["error"], "state": state}
        if state["unmerged"]:
            return {"ok": False, "message": "当前已有未解决的冲突，请先处理或撤销更新", "state": state}
        if not state["behind"]:
            return {"ok": True, "message": "已是最新，无需更新", "state": state}
        record = {"backup_sha": state["head"], "merge_sha": "", "stash_ref": "", "stash_popped": False, "phase": "merging"}
        self._save_record(record)
        if state["dirty"]:
            stash = self.git(["stash", "push", "-m", f"launcher-update-{time.strftime('%Y%m%d-%H%M%S')}"])
            if stash["code"] != 0:
                record["phase"] = "none"
                self._save_record(record)
                return {"ok": False, "message": f"暂存本地改动失败：{(stash['err'] or stash['out']).strip()[:200]}", "state": self.state()}
            record["stash_ref"] = self.git(["rev-parse", "stash@{0}"])["out"].strip()
            self._save_record(record)
        ff = self.git(["merge", "--ff-only", f"origin/{state['branch']}"])
        if ff["code"] != 0:
            ff = self.git(GIT_IDENTITY + ["merge", "--no-edit", f"origin/{state['branch']}"])
        record["merge_sha"] = self.head()
        if ff["code"] != 0:
            if self.unmerged():
                record["phase"] = "merge_conflict"
                self._save_record(record)
                return {"ok": False, "conflicts": True, "message": "更新出现冲突，请逐文件选择保留哪边", "state": self.state()}
            record["phase"] = "failed"
            self._save_record(record)
            return {"ok": False, "message": f"合并失败：{(ff['err'] or ff['out']).strip()[:300]}", "state": self.state()}
        return self._finish(record, "更新完成，建议点【重启】让新代码生效")

    def _finish(self, record, message):
        if record.get("stash_ref") and not record.get("stash_popped"):
            pop = self.git(["stash", "pop"])
            if pop["code"] != 0:
                if self.unmerged():
                    record["phase"] = "stash_conflict"
                    self._save_record(record)
                    return {"ok": False, "conflicts": True, "message": "更新本身成功，但放回本地未提交改动时出现冲突，请逐文件选择", "state": self.state()}
            else:
                record["stash_popped"] = True
        record["phase"] = "done"
        self._save_record(record)
        return {"ok": True, "message": message, "state": self.state()}

    def _drop_stash_by_sha(self, sha):
        """git stash drop 只认 stash@{n} 形式的 ref，不认裸 commit sha；按列表匹配后删。"""
        if not sha:
            return
        listing = self.git(["stash", "list", "--format=%gd\t%H"])
        for line in listing["out"].splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1] == sha:
                self.git(["stash", "drop", parts[0]])
                return

    def resolve(self, name, side):
        """side: ours=保留我的 / theirs=用新版；name=__all__ 批量。"""
        record = self._record()
        files = self.unmerged()
        if not files:
            return {"ok": False, "message": "当前没有冲突文件", "state": self.state()}
        if name != "__all__":
            if name not in files:
                return {"ok": False, "message": f"{name} 不在冲突列表中", "state": self.state()}
            files = [name]
        # stash pop 的冲突里，"我的未提交改动"在 --theirs 一侧，语义要翻转
        real = {"ours": "ours", "theirs": "theirs"}
        if record.get("phase") == "stash_conflict":
            real = {"ours": "theirs", "theirs": "ours"}
        if side not in ("ours", "theirs"):
            return {"ok": False, "message": "side 只能是 ours/theirs", "state": self.state()}
        for path in files:
            q = self.git(["checkout", f"--{real[side]}", "--", path])
            if q["code"] != 0:
                return {"ok": False, "message": f"{path} 处理失败：{(q['err'] or q['out']).strip()[:200]}", "state": self.state()}
            self.git(["add", "--", path])
        left = self.unmerged()
        if left:
            return {"ok": True, "message": f"已处理 {len(files)} 个，还剩 {len(left)} 个", "state": self.state()}
        if record.get("phase") == "merge_conflict":
            commit = self.git(GIT_IDENTITY + ["commit", "--no-edit"])
            if commit["code"] != 0:
                return {"ok": False, "message": f"冲突已选完但提交失败：{(commit['err'] or commit['out']).strip()[:200]}", "state": self.state()}
            record["merge_sha"] = self.head()
        elif record.get("phase") == "stash_conflict":
            record["stash_popped"] = True
            self._drop_stash_by_sha(record.get("stash_ref"))
        return self._finish(record, "冲突全部处理完毕")

    def undo(self):
        record = self._record()
        backup = record.get("backup_sha") or ""
        if not backup or self.git(["rev-parse", "--verify", backup])["code"] != 0:
            return {"ok": False, "message": "没有可撤销的更新备份点", "state": self.state()}
        phase = record.get("phase") or ""
        if phase == "merge_conflict":
            self.git(["merge", "--abort"])
        head = self.head()
        if head not in (backup, record.get("merge_sha")):
            return {"ok": False, "message": "更新之后又有新的提交，不敢自动回退，请手动处理", "state": self.state()}
        carry_ref = ""
        restore_ref = ""
        stash_pending = record.get("stash_ref") and not record.get("stash_popped")
        if self.unmerged():
            # stash pop 冲突形态：先把工作区清回更新版（stash 条目仍在），回退后再 apply 回来
            for path in self.unmerged():
                self.git(["checkout", "--ours", "--", path])
                self.git(["add", "--", path])
            restore_ref = record.get("stash_ref") or ""
        elif self.tracked_dirty():
            stash = self.git(["stash", "push", "-m", "launcher-undo"])
            if stash["code"] != 0:
                return {"ok": False, "message": f"暂存当前改动失败：{(stash['err'] or stash['out']).strip()[:200]}", "state": self.state()}
            carry_ref = self.git(["rev-parse", "stash@{0}"])["out"].strip()
        elif stash_pending:
            restore_ref = record.get("stash_ref") or ""
        if head != backup:
            reset = self.git(["reset", "--hard", backup])
            if reset["code"] != 0:
                return {"ok": False, "message": f"回退失败：{(reset['err'] or reset['out']).strip()[:200]}", "state": self.state()}
        if carry_ref:
            pop = self.git(["stash", "pop"])
            if pop["code"] != 0:
                return {"ok": False, "message": "代码已回退，但放回你的改动失败，请到 git stash 里手动找回", "state": self.state()}
        if restore_ref:
            apply = self.git(["stash", "apply", restore_ref])
            if apply["code"] != 0:
                return {"ok": False, "message": "代码已回退，但放回你的改动出现冲突，stash 条目保留，请手动处理", "state": self.state()}
            self._drop_stash_by_sha(restore_ref)
        record["phase"] = "undone"
        self._save_record(record)
        return {"ok": True, "message": "已撤销更新，回到更新前的状态", "state": self.state()}

    def update_record(self):
        return self._record()

# ---------------------------------------------------------------- HTTP 控制台

HEARTBEAT = {"last": time.time(), "loaded": False}


class Handler(BaseHTTPRequestHandler):
    server_version = "InfiniteCanvasLauncher/1.0"

    def log_message(self, *args):  # 静音默认访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:  # noqa: BLE001
            return {}

    def _payload(self):
        git = GitSync()
        version_path = os.path.join(BASE_DIR, "VERSION")
        return {
            "service": service_status(),
            "app": {"version": "", "launcher_port": self.server.server_address[1]},
            "git": git.state(),
            "update": git.update_record(),
        }

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                with io.open(HTML_PATH, "r", encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                self._send(500, f"launcher.html 读取失败：{exc}", "text/plain; charset=utf-8")
            return
        if path == "/api/state":
            self._send(200, self._payload())
            return
        if path == "/api/logs":
            offset = 0
            try:
                offset = int(urllib.parse.parse_qs(parsed.query).get("offset", ["0"])[0])
            except ValueError:
                pass
            size, text, new_offset = 0, "", offset
            try:
                with io.open(SERVER_LOG, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    if offset > size:
                        offset = 0
                    tail_start = max(offset, size - 200000)
                    f.seek(tail_start)
                    text = f.read().decode("utf-8", errors="replace")
                    new_offset = size
            except FileNotFoundError:
                pass
            self._send(200, {"text": text, "offset": new_offset})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._json_body()
        git = GitSync()
        if path == "/api/ping":
            HEARTBEAT["last"] = time.time()
            HEARTBEAT["loaded"] = True
            self._send(200, {"ok": True})
            return
        if path == "/api/shutdown":
            self._send(200, {"ok": True})
            threading.Timer(0.4, lambda: os._exit(0)).start()
            return
        if path == "/api/service/start":
            self._send(200, start_service())
            return
        if path == "/api/service/stop":
            self._send(200, stop_service())
            return
        if path == "/api/service/restart":
            self._send(200, restart_service())
            return
        if path == "/api/git/fetch":
            self._send(200, git.fetch())
            return
        if path == "/api/git/update":
            self._send(200, git.update())
            return
        if path == "/api/git/push":
            self._send(200, git.push())
            return
        if path == "/api/git/undo":
            self._send(200, git.undo())
            return
        if path == "/api/git/resolve":
            self._send(200, git.resolve(str(body.get("file") or ""), str(body.get("side") or "")))
            return
        self._send(404, {"error": "not found"})

# ---------------------------------------------------------------- 入口

def find_edge():
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        root = os.environ.get(env, "")
        if root:
            candidates.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
            candidates.append(os.path.join(root, "Microsoft", "Edge", "msedge.exe"))
    candidates.append(os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def open_window(url):
    edge = find_edge()
    if edge:
        try:
            subprocess.Popen([edge, f"--app={url}", "--window-size=1120,820"])
            return "edge"
        except Exception:  # noqa: BLE001
            pass
    webbrowser.open(url)
    return "browser"


def bind_server():
    last_error = None
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 11):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            httpd.daemon_threads = True
            return httpd
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"无法在 127.0.0.1:{PREFERRED_PORT}~{PREFERRED_PORT + 10} 监听：{last_error}")


def watchdog(httpd):
    while True:
        time.sleep(5)
        now = time.time()
        if HEARTBEAT["loaded"]:
            if now - HEARTBEAT["last"] > 120:
                os._exit(0)
        elif now - httpd.started_at > 90:
            os._exit(0)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    httpd = bind_server()
    httpd.started_at = time.time()
    port = httpd.server_address[1]
    write_json(PORT_FILE, {"port": port, "pid": os.getpid()})
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=watchdog, args=(httpd,), daemon=True).start()
    open_window(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
