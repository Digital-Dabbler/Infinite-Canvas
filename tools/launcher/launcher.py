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
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(BASE_DIR, "logs")
SERVER_LOG = os.path.join(LOG_DIR, "launcher_server.log")
LAUNCHER_LOG = os.path.join(LOG_DIR, "launcher.log")
SERVICE_STATE = os.path.join(LOG_DIR, "launcher_server.json")
# 本启动器亲手启动的服务：run_id 由环境变量带进服务进程，pid 用于 stop/quit 授权。
# 只有 pid 命中这里才允许停止，避免把用户自己双击 bat 起来的服务一起关掉。
SESSION_STATE = os.path.join(LOG_DIR, "launcher_session.json")
RUN_ID_ENV = "INFINITE_CANVAS_LAUNCHER_RUN_ID"
PORT_FILE = os.path.join(LOG_DIR, "launcher_port.txt")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.html")

SERVICE_PORT = 3000
PREFERRED_PORT = 3100

# 启动器没有控制台，任何子进程（netstat/git/powershell/tasklist）都必须隐藏窗口。
# 不带的后果：页面每 2.5 秒轮询状态时会在桌面弹出一排黑框，用户看到的就是"窗口一直闪"。
if os.name == "nt":
    SUBPROCESS_HIDDEN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
else:
    SUBPROCESS_HIDDEN = {}
GIT_IDENTITY = ["-c", "user.name=InfiniteCanvasLauncher", "-c", "user.email=launcher@local"]

# ---------------------------------------------------------------- 工具函数

def log_line(msg):
    """启动器自身没有控制台，所有关键动作写 logs/launcher.log 便于排查。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with io.open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def message_box(text, title="无限画布 · 启动器"):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:  # noqa: BLE001
        pass

def run_cmd(args, cwd=BASE_DIR, timeout=60):
    try:
        proc = subprocess.run(
            args, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **SUBPROCESS_HIDDEN,
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


def read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


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
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        # QueryFullProcessImageNameW 在 kernel32（不是 advapi32）；restype 必须显式声明，
        # 否则 64 位系统上句柄/返回值按 32 位 int 截断
        kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
            ctypes.wintypes.LPWSTR, ctypes.POINTER(ctypes.wintypes.DWORD),
        ]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = ctypes.wintypes.DWORD(4096)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""


def pid_env_value(pid, name):
    """读取指定进程的环境变量（NtQueryInformationProcess 方式，只依赖 ntdll）。

    用来认领「是不是本启动器起的服务」：服务进程继承了我们注入的 run_id，即使 pid 记录
    没写成，也能凭这个变量确认归属。普通用户对同权限进程可读；读不到返回 ""。
    """
    if os.name != "nt" or not pid:
        return ""
    try:
        import ctypes.wintypes as wt

        class UNICODE_STRING(ctypes.Structure):
            _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_void_p)]

        class PBI(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2), ("UniqueProcessId", ctypes.c_size_t),
                ("Reserved3", ctypes.c_void_p),
            ]

        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        kernel32.OpenProcess.restype = wt.HANDLE
        kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        ntdll.NtQueryInformationProcess.argtypes = [
            wt.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.ReadProcessMemory.restype = wt.BOOL
        kernel32.ReadProcessMemory.argtypes = [
            wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        ]
        handle = kernel32.OpenProcess(0x0410, False, int(pid))  # QUERY_INFORMATION | VM_READ
        if not handle:
            return ""
        try:
            pbi = PBI()
            got = ctypes.c_ulong(0)
            if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(got)) != 0:
                return ""
            read = ctypes.c_size_t(0)
            params = ctypes.c_void_p()
            if not kernel32.ReadProcessMemory(handle, ctypes.c_void_p(pbi.PebBaseAddress + 0x20),
                                              ctypes.byref(params), 8, ctypes.byref(read)):
                return ""
            # RTL_USER_PROCESS_PARAMETERS.Environment 在 x64 上位于 +0x80
            env_ptr = ctypes.c_void_p()
            if not kernel32.ReadProcessMemory(handle, ctypes.c_void_p(params.value + 0x80),
                                              ctypes.byref(env_ptr), 8, ctypes.byref(read)):
                return ""
            if not env_ptr.value:
                return ""
            head = ctypes.create_string_buffer(65536)
            if not kernel32.ReadProcessMemory(handle, env_ptr, head, 65536, ctypes.byref(read)):
                return ""
            text = head.raw[: read.value].decode("utf-16-le", "ignore")
            target = name.lower() + "="
            for item in text.split("\x00"):
                if item.lower().startswith(target):
                    return item.split("=", 1)[1]
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""


def listening_pid(port=SERVICE_PORT):
    """netstat 找监听端口进程（本机 WMI/tasklist 会卡死，必须避开）。

    只认端口号精确相等且状态为 LISTENING 的行：早先按「地址以 :3000 结尾 + 对端 0.0.0.0:0」
    匹配，漏掉了 127.0.0.1:3000 这类监听，导致明明有服务却报"未运行"。
    """
    res = run_cmd(["netstat", "-ano", "-p", "tcp"], timeout=20)
    if res["code"] != 0:
        return None
    suffix = f":{port}"
    for line in res["out"].splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper() == "TCP" and parts[1].endswith(suffix) \
                and parts[3].upper() == "LISTENING":
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

def open_external(url):
    """把工作台交给系统默认浏览器打开（启动器窗口自己不能变成浏览器标签页）。"""
    url = str(url or "").strip()
    if not re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url):
        return {"ok": False, "message": "只允许打开本机工作台地址"}
    try:
        os.startfile(url)  # noqa: S606 - 已按白名单校验，只会是本机 http 地址
    except OSError as exc:
        return {"ok": False, "message": f"打开浏览器失败：{exc}"}
    return {"ok": True, "message": "已用默认浏览器打开工作台"}


def quit_launcher():
    """退出启动器：只关掉它自己启动的服务，别人的服务一律不碰。"""
    stopped = stop_own_service()
    if stopped is None:
        message = "启动器已退出；正在运行的服务不是本启动器启动的，保持原样"
    else:
        message = stopped.get("message") or "服务已停止"
    log_line(f"main: 用户退出启动器（{message}）")
    return {"ok": True, "message": message, "state": service_status()}


def owns_pid(pid):
    """pid 是不是本启动器亲手起的服务：会话记录里必须有 pid，且该服务仍带着同一个 run_id。

    服务进程在 main.py 跑起来后会把会话记录补写为自己的真实 pid；期间由环境变量认领，
    这样即使「启动」响应在端口就绪前超时，也不会被误判成别人的服务。
    """
    if not pid:
        return False
    session = read_json(SESSION_STATE, {})
    run_id = str(session.get("run_id") or "")
    if not run_id:
        return False
    if int(session.get("pid") or 0) == pid:
        return True
    if int(session.get("pid") or 0) != 0:
        return False
    return pid_env_value(pid, RUN_ID_ENV) == run_id


def service_status():
    pid = listening_pid()
    state = read_json(SERVICE_STATE, {})
    session = read_json(SESSION_STATE, {})
    running = pid is not None
    managed = owns_pid(pid)
    ours = bool(pid and is_project_python(pid_image_path(pid)))
    external = running and not ours
    return {
        "running": running,
        "ours": ours,
        "managed": managed,
        "external": external,
        "pid": pid or 0,
        "occupant_pid": pid or 0,
        "port": SERVICE_PORT,
        "url": f"http://127.0.0.1:{SERVICE_PORT}",
        "lan": lan_address() if running else "",
        "started_at": state.get("started_at", "") if (running and managed) else "",
    }


def start_service():
    current = service_status()
    if current["running"]:
        return {"ok": True, "message": "服务已在运行", "state": current}
    os.makedirs(LOG_DIR, exist_ok=True)
    exe = project_python()
    if not os.path.exists(exe):
        return {"ok": False, "message": "找不到可用的 Python 解释器", "state": current}
    # 先记下"这一轮启动器亲手起的服务"，再拉起进程：
    # 退出启动器/停止服务只认这个会话，否则端口上任意一个项目 python 都会被当成自己的。
    run_id = uuid.uuid4().hex
    session = {"run_id": run_id, "pid": 0, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "exe": exe}
    write_json(SESSION_STATE, session)
    logf = io.open(SERVER_LOG, "ab")
    flags = 0
    if os.name == "nt":
        # DETACHED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        flags = 0x00000008 | 0x00000200 | 0x08000000
    try:
        proc = subprocess.Popen(
            [exe, "main.py"], cwd=BASE_DIR,
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            creationflags=flags,
            env=dict(os.environ, **{RUN_ID_ENV: run_id}),
        )
    except Exception as exc:  # noqa: BLE001
        logf.close()
        end_session(run_id)
        return {"ok": False, "message": f"启动失败：{exc}", "state": current}
    session["pid"] = proc.pid
    if session_owner(run_id) == run_id:
        write_json(SESSION_STATE, session)
    write_json(SERVICE_STATE, {"pid": proc.pid, "run_id": run_id, "started_at": session["started_at"], "exe": exe})
    ready = wait_port_ready(SERVICE_PORT, timeout=180)
    return {
        "ok": ready,
        "message": "服务已就绪" if ready else "进程已启动，但 3 分钟内端口未就绪，请查看日志",
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


def session_owner(run_id):
    """这一轮启动器是否仍然拥有该服务（防止"启动-失败-又启动"的竞态把新会话认成旧的）。"""
    return str(read_json(SESSION_STATE, {}).get("run_id") or "") == str(run_id)


def end_session(run_id):
    """结束会话认领；run_id 为空表示无条件结束。"""
    session = read_json(SESSION_STATE, {})
    if run_id and str(session.get("run_id") or "") != str(run_id):
        return
    session["pid"] = 0
    write_json(SESSION_STATE, session)


def stop_own_service():
    """只停止本启动器亲手启动的服务；不是自己起的返回 None（调用方只提示，不动手）。"""
    pid = listening_pid()
    if pid is None:
        # 端口上已经没有服务：结束认领（别删 run_id，否则下次启动的竞态保护也失效）
        end_session()
        return {"ok": True, "message": "服务本来就没有在运行", "state": service_status()}
    session = read_json(SESSION_STATE, {})
    if not owns_pid(pid):
        return None
    exe = pid_image_path(pid)
    if not is_project_python(exe):
        return None
    terminate_pid(pid)
    end_session(session.get("run_id"))
    freed = wait_port_free(SERVICE_PORT)
    return {"ok": freed, "message": "服务已停止" if freed else "已发送停止，但端口仍被占用，请查看任务管理器", "state": service_status()}


def stop_service():
    pid = listening_pid()
    if pid is None:
        return {"ok": True, "message": "服务本来就没有在运行", "state": service_status()}
    result = stop_own_service()
    if result is None:
        # 端口上的是用户自己双击 bat 起的、或上一次启动器留下的服务：只提示，绝不动手。
        # （曾经因此把用户正在使用的服务杀掉过。）
        return {
            "ok": False,
            "message": f"端口 {SERVICE_PORT} 上的服务不是本启动器启动的，为避免误关已跳过；如需停止请到任务管理器结束 PID {pid}",
            "state": service_status(),
        }
    return result


def restart_service():
    stopped = stop_own_service()
    if stopped is None:
        # 不是本启动器起的服务，重启会把它杀掉；这种情况请用户自己处理，别替用户做决定
        return stop_service()
    if not stopped["ok"]:
        return stopped
    return start_service()

# ---------------------------------------------------------------- git 同步

class GitSync:
    def __init__(self, cwd=BASE_DIR):
        self.cwd = cwd
        self.record_path = os.path.join(cwd, "logs", "launcher_update.json")
        # 缓存按仓库路径隔离：不同 cwd 的实例（测试里的临时仓库）绝不能互相读到状态。
        self._state_cache = None
        self._state_at = 0.0

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
        # 一次请求里可能被调多次（fetch/push/update 的返回值都带 state），
        # 缓存到显式 invalidate() 为止，避免一次点击连跑几十条 git。
        if self._state_cache is not None:
            return self._state_cache
        info = self._compute_state()
        self._state_cache = info
        return info

    def invalidate(self):
        self._state_cache = None

    def _fresh(self, result):
        """动作类方法统一收尾：丢掉旧缓存，再把返回值里的 state 换成动作之后的真实状态。

        只在动作开始前偶尔 invalidate 是不够的——干净工作区做 ff 更新时全程不会触发
        invalidate，页面就会拿"更新前 behind=1"的旧快照，误以为更新没生效。
        """
        self.invalidate()
        if isinstance(result, dict):
            result["state"] = self.state()
        return result

    def _current(self):
        """返回值里的 state 一律现算：动作中途就拼好的 dict 不能把动作前的快照带出去。"""
        self._state_cache = None
        return self._compute_state()

    def _compute_state(self):
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
        self.invalidate()
        res = self.git(["fetch", "origin", "--prune"], timeout=120)
        if res["code"] != 0:
            return self._fresh({"ok": False, "message": f"fetch 失败：{(res['err'] or res['out']).strip()[:300]}"})
        return self._fresh({"ok": True, "message": "已获取远端最新状态"})

    def push(self):
        branch = self.branch()
        self.invalidate()
        res = self.git(["push", "origin", f"HEAD:{branch}"], timeout=180)
        ok = res["code"] == 0
        note = "" if not self.tracked_dirty() else "（注意：未提交的改动不在推送范围内）"
        return self._fresh({"ok": ok, "message": ("已推送 " if ok else "推送失败：") + (branch if ok else (res["err"] or res["out"]).strip()[:300]) + note})

    def update(self):
        """一键更新：脏改动 stash → 记录备份点 → ff-only/merge origin/分支 → stash pop。"""
        state = self._current()
        if state["error"]:
            return {"ok": False, "message": state["error"], "state": state}
        if state["unmerged"]:
            return {"ok": False, "message": "当前已有未解决的冲突，请先处理或撤销更新", "state": state}
        if not state["behind"]:
            return {"ok": True, "message": "已是最新，无需更新", "state": state}
        record = {"backup_sha": state["head"], "merge_sha": "", "stash_ref": "", "stash_popped": False, "phase": "merging"}
        self._save_record(record)
        if state["dirty"]:
            self.invalidate()
            stash = self.git(["stash", "push", "-m", f"launcher-update-{time.strftime('%Y%m%d-%H%M%S')}"])
            if stash["code"] != 0:
                record["phase"] = "none"
                self._save_record(record)
                return {"ok": False, "message": f"暂存本地改动失败：{(stash['err'] or stash['out']).strip()[:200]}", "state": self._current()}
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
                return {"ok": False, "conflicts": True, "message": "更新出现冲突，请逐文件选择保留哪边", "state": self._current()}
            record["phase"] = "failed"
            self._save_record(record)
            return {"ok": False, "message": f"合并失败：{(ff['err'] or ff['out']).strip()[:300]}", "state": self._current()}
        return self._fresh(self._finish(record, "更新完成，建议点【重启】让新代码生效"))

    def _finish(self, record, message):
        if record.get("stash_ref") and not record.get("stash_popped"):
            pop = self.git(["stash", "pop"])
            if pop["code"] != 0:
                if self.unmerged():
                    record["phase"] = "stash_conflict"
                    self._save_record(record)
                    return {"ok": False, "conflicts": True, "message": "更新本身成功，但放回本地未提交改动时出现冲突，请逐文件选择", "state": self._current()}
            else:
                record["stash_popped"] = True
        record["phase"] = "done"
        self._save_record(record)
        return {"ok": True, "message": message, "state": self._current()}

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
        self.invalidate()
        record = self._record()
        files = self.unmerged()
        if not files:
            return {"ok": False, "message": "当前没有冲突文件", "state": self._current()}
        if name != "__all__":
            if name not in files:
                return {"ok": False, "message": f"{name} 不在冲突列表中", "state": self._current()}
            files = [name]
        # stash pop 的冲突里，"我的未提交改动"在 --theirs 一侧，语义要翻转
        real = {"ours": "ours", "theirs": "theirs"}
        if record.get("phase") == "stash_conflict":
            real = {"ours": "theirs", "theirs": "ours"}
        if side not in ("ours", "theirs"):
            return {"ok": False, "message": "side 只能是 ours/theirs", "state": self._current()}
        for path in files:
            self.invalidate()
            q = self.git(["checkout", f"--{real[side]}", "--", path])
            if q["code"] != 0:
                return {"ok": False, "message": f"{path} 处理失败：{(q['err'] or q['out']).strip()[:200]}", "state": self._current()}
            self.git(["add", "--", path])
        left = self.unmerged()
        if left:
            return {"ok": True, "message": f"已处理 {len(files)} 个，还剩 {len(left)} 个", "state": self._current()}
        if record.get("phase") == "merge_conflict":
            commit = self.git(GIT_IDENTITY + ["commit", "--no-edit"])
            if commit["code"] != 0:
                return {"ok": False, "message": f"冲突已选完但提交失败：{(commit['err'] or commit['out']).strip()[:200]}", "state": self._current()}
            record["merge_sha"] = self.head()
        elif record.get("phase") == "stash_conflict":
            record["stash_popped"] = True
            self._drop_stash_by_sha(record.get("stash_ref"))
        return self._fresh(self._finish(record, "冲突全部处理完毕"))


    def undo(self):
        self.invalidate()
        record = self._record()
        backup = record.get("backup_sha") or ""
        if not backup or self.git(["rev-parse", "--verify", backup])["code"] != 0:
            return {"ok": False, "message": "没有可撤销的更新备份点", "state": self._current()}
        phase = record.get("phase") or ""
        if phase == "merge_conflict":
            self.git(["merge", "--abort"])
        head = self.head()
        if head not in (backup, record.get("merge_sha")):
            return {"ok": False, "message": "更新之后又有新的提交，不敢自动回退，请手动处理", "state": self._current()}
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
            self.invalidate()
            stash = self.git(["stash", "push", "-m", "launcher-undo"])
            if stash["code"] != 0:
                return {"ok": False, "message": f"暂存当前改动失败：{(stash['err'] or stash['out']).strip()[:200]}", "state": self._current()}
            carry_ref = self.git(["rev-parse", "stash@{0}"])["out"].strip()
        elif stash_pending:
            restore_ref = record.get("stash_ref") or ""
        if head != backup:
            reset = self.git(["reset", "--hard", backup])
            if reset["code"] != 0:
                return {"ok": False, "message": f"回退失败：{(reset['err'] or reset['out']).strip()[:200]}", "state": self._current()}
        if carry_ref:
            pop = self.git(["stash", "pop"])
            if pop["code"] != 0:
                return {"ok": False, "message": "代码已回退，但放回你的改动失败，请到 git stash 里手动找回", "state": self._current()}
        if restore_ref:
            apply = self.git(["stash", "apply", restore_ref])
            if apply["code"] != 0:
                return {"ok": False, "message": "代码已回退，但放回你的改动出现冲突，stash 条目保留，请手动处理", "state": self._current()}
            self._drop_stash_by_sha(restore_ref)
        record["phase"] = "undone"
        self._save_record(record)
        return self._fresh({"ok": True, "message": "已撤销更新，回到更新前的状态"})

    def update_record(self):
        return self._record()

# ---------------------------------------------------------------- HTTP 控制台

HEARTBEAT = {"last": time.time(), "loaded": False}

# 页面轮询的状态快照：后台线程每 SNAPSHOT_INTERVAL 秒重采一次，页面只读它。
STATE_CACHE = os.path.join(LOG_DIR, "launcher_state.json")
SNAPSHOT_INTERVAL = 3.0
SNAPSHOT_LOCK = threading.Lock()


def build_snapshot():
    """采集一次完整状态。只在后台线程按节奏调用，绝不在请求线程里调用。"""
    with SNAPSHOT_LOCK:
        git = GitSync()
        try:
            git_state = git.state()
            update_record = git.update_record()
        except Exception as exc:  # noqa: BLE001
            log_line(f"snapshot: git 状态读取失败：{exc}")
            git_state, update_record = {"error": str(exc)}, {}
        return {
            "service": service_status(),
            "app": {"version": read_text(os.path.join(BASE_DIR, "VERSION")).strip()[:40]},
            "git": git_state,
            "update": update_record,
            "sampled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def snapshot_worker():
    """后台定时采集；出错也不退出，下一次继续。"""
    while True:
        try:
            write_json(STATE_CACHE, build_snapshot())
        except Exception as exc:  # noqa: BLE001
            log_line(f"snapshot_worker: {exc}")
        time.sleep(SNAPSHOT_INTERVAL)


def refresh_snapshot():
    """动作类接口（启停/更新/推拉）改完立刻刷新，页面不用等下一个采集点。"""
    try:
        snapshot = build_snapshot()
        write_json(STATE_CACHE, snapshot)
        return snapshot
    except Exception as exc:  # noqa: BLE001
        log_line(f"refresh_snapshot: {exc}")
        return {}


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
        # 页面轮询只读缓存：git/netstat 由后台线程每 3 秒采集一次。
        # 以前在这里直接跑 git，2.5 秒一轮会在桌面闪出一排黑框。
        snapshot = read_json(STATE_CACHE, {})
        if not snapshot:
            snapshot = build_snapshot()
            write_json(STATE_CACHE, snapshot)
        snapshot = dict(snapshot)
        snapshot.setdefault("service", {})["launcher_port"] = self.server.server_address[1]
        version_path = os.path.join(BASE_DIR, "VERSION")
        snapshot.setdefault("app", {})["version"] = read_text(version_path).strip()[:40]
        snapshot["app"]["launcher_port"] = self.server.server_address[1]
        return snapshot

    def _log_request(self, method, path):
        # 排障用：每种路径只记首次，避免轮询刷屏
        seen = Handler._seen_paths
        if path not in seen:
            seen.add(path)
            log_line(f"request: {method} {path}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        self._log_request("GET", path)
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

    _seen_paths = set()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        self._log_request("POST", path)
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
        if path == "/api/open-url":
            self._send(200, open_external(str(body.get("url") or "")))
            return
        if path == "/api/quit":
            # 「退出启动器」= 退出启动器并一并关掉它自己启动的服务；右上角关窗只关窗口、服务继续。
            result = quit_launcher()
            self._send(200, result)
            threading.Timer(0.6, lambda: os._exit(0)).start()
            return
        if path == "/api/service/start":
            result = start_service(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/service/stop":
            result = stop_service(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/service/restart":
            result = restart_service(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/git/fetch":
            result = git.fetch(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/git/update":
            result = git.update(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/git/push":
            result = git.push(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/git/undo":
            result = git.undo(); refresh_snapshot(); self._send(200, result)
            return
        if path == "/api/git/resolve":
            result = git.resolve(str(body.get("file") or ""), str(body.get("side") or ""))
            refresh_snapshot(); self._send(200, result)
            return
        self._send(404, {"error": "not found"})

# ---------------------------------------------------------------- 入口

def launcher_script():
    return os.path.join(BASE_DIR, "tools", "launcher", "launcher.py")


def window_script():
    return os.path.join(BASE_DIR, "tools", "launcher", "window.py")


def spawn_window(url):
    """用同目录 pythonw 拉起原生窗口进程（不开任何浏览器）。

    历史教训（本机实测，不要再走回头路）：
    ① msedge/chrome --app=URL → 本机浏览器以管理员权限运行，普通权限进程拉 --app
       会被系统拦截并反复弹"Edge 提权冲突"；
    ② webbrowser.open()/os.startfile() → 默认浏览器 ProgId 残留指向已卸载的 msedge，
       返回成功却什么都不发生；
    ③ 结论：窗口必须由我们自己开（tools/launcher/window.py + 内置 WebView2 组件）。
    """
    exe = sys.executable if sys.executable.lower().endswith(".exe") else ""
    if not exe.lower().endswith("pythonw.exe"):
        candidate = os.path.join(BASE_DIR, "python", "pythonw.exe")
        if os.path.exists(candidate):
            exe = candidate
    if not exe:
        log_line("window: 找不到 pythonw.exe，无法打开窗口")
        return False
    try:
        proc = subprocess.Popen(
            [exe, window_script(), url],
            cwd=BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **SUBPROCESS_HIDDEN,
        )
    except Exception as exc:  # noqa: BLE001
        log_line(f"window: 拉起窗口进程失败：{exc}")
        return False
    log_line(f"window: 已拉起原生窗口 pid {proc.pid}")
    return True

def live_launcher():
    """启动器单实例：返回已在运行的启动器端口；没有则返回 0。"""
    data = read_json(PORT_FILE, {})
    try:
        port = int(data.get("port") or 0)
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        return 0
    if not port or not pid or pid == os.getpid():
        return 0
    # 用 /api/ping 做探活：它不碰 git、不建快照，只在就是我们的服务时秒回。
    ctypes.windll.winmm.timeBeginPeriod(1)
    try:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2.0).close()
        except OSError:
            return 0  # 端口没人监听，说明旧启动器已经退出了
        import urllib.request
        body = b"{}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ping",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                return port
    except Exception as exc:  # noqa: BLE001
        log_line(f"main: 单实例探测失败（端口 {port}）：{exc}")
    finally:
        ctypes.windll.winmm.timeEndPeriod(1)
    return 0

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
    """兜底看门狗。

    关窗后启动器必须继续驻留：服务照常跑，再双击启动器会直接重开窗口。
    只有「窗口始终没连上启动器」（窗口进程没起来）才在 180 秒后退出，
    否则后台会一直挂一个再也打不开窗口的空启动器。
    """
    while True:
        time.sleep(5)
        if not HEARTBEAT["loaded"] and time.time() - httpd.started_at > 180:
            log_line("watchdog: 窗口始终没有连上启动器，启动器退出（详见上方 window 日志）")
            os._exit(0)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    # 已经在跑就只补开一个窗口，不再起第二个服务（避免重复窗口和端口漂移）
    running = live_launcher()

    if running:
        url = f"http://127.0.0.1:{running}"
        log_line(f"main: 启动器已在 {url} 运行，只重新打开窗口")
        spawn_window(url)
        return
    httpd = bind_server()
    httpd.started_at = time.time()
    port = httpd.server_address[1]
    write_json(PORT_FILE, {"port": port, "pid": os.getpid()})
    url = f"http://127.0.0.1:{port}"
    log_line(f"main: 启动器已监听 {url} (pid {os.getpid()})")
    threading.Thread(target=watchdog, args=(httpd,), daemon=True).start()
    threading.Thread(target=spawn_window, args=(url,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
