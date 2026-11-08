# -*- coding: utf-8 -*-
"""无限画布 · 启动器主窗口（Windows 原生窗口，元素网络视图）。

为什么不用浏览器：
- 曾用 msedge/chrome --app=URL 开"独立窗口"，本机浏览器以管理员权限运行，
  普通权限进程去拉它会被 Windows 拦住，反复弹"Edge 提权冲突"框；
- 改用 webbrowser.open() 也一样：本机默认浏览器注册的是 Edge，但 Edge 已卸载，
  调用返回成功，实际什么都没打开。
本文件用 .NET 的 WinForms + WebView2 直接开原生窗口，不经过任何浏览器，
也就不存在权限、默认浏览器、提权冲突这些问题。

行为约定（用户已确认）：
- 关窗口 = 退出启动器，服务继续在后台运行；
- 记住上次的窗口位置和大小（logs/launcher_window.json）。
"""
import io
import json
import os
import sys
import threading
import time
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LAUNCHER_LOG = os.path.join(LOG_DIR, "launcher.log")
WINDOW_STATE = os.path.join(LOG_DIR, "launcher_window.json")

TITLE = "无限画布 · 启动器"
DEFAULT_SIZE = (1200, 820)
MIN_SIZE = (900, 620)

# pythonnet 需要的运行时环境变量，必须在 import clr 之前设置。
# 本机的 .NET Framework 4.8 跑不了 pythonnet 3.0.3 的 Python.Runtime.dll，
# 必须用 coreclr（.NET 9）。不要改回 netfx。
os.environ.setdefault("PYTHONNET_RUNTIME", "coreclr")
os.environ.setdefault("DOTNET_ROOT", r"C:\Program Files\dotnet")
_WINDESKTOP = _find_windesktop = os.path.join(
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    "dotnet", "shared", "Microsoft.WindowsDesktop.App",
)


def log_line(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with io.open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] window: {msg}\n")
    except OSError:
        pass


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


def pick_windesktop_runtime():
    """挑一个可用的 WindowsDesktop 运行时版本，返回 (目录, 版本)。

    【重要】WindowsDesktop 里的程序集默认不解析：pythonnet 的加载器只挂
    Microsoft.NETCore.App，所以 WebView2 控件会报"找不到 System.Windows.Forms"。
    这里不靠 PYTHONNET_ADDITIONAL_DEPS 环境变量（实测对文件夹不生效），
    而是下面用 AssemblyResolve 手动从该目录 LoadFrom。
    """
    if not os.path.isdir(_WINDESKTOP):
        return "", ""
    versions = []
    for name in os.listdir(_WINDESKTOP):
        parts = [p for p in name.split(".") if p.isdigit()]
        if len(parts) >= 2:
            versions.append((tuple(int(p) for p in parts), name))
    if not versions:
        return "", ""
    versions.sort(reverse=True)
    for _key, name in versions:
        dll = os.path.join(_WINDESKTOP, name, "System.Windows.Forms.dll")
        if os.path.exists(dll):
            return os.path.join(_WINDESKTOP, name), name
    return "", ""


def find_webview2_dll():
    """找 WebView2 托管程序集；找不到时认为是"没装 WebView2 组件"。"""
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env, "")
        if root:
            candidates.append(os.path.join(root, "Microsoft", "EdgeWebView", "Application"))
            candidates.append(os.path.join(root, "Microsoft", "Edge", "Application"))
    # 兼容：把 dll 放在 tools/launcher/webview2/ 下也能用
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webview2"))
    for root in candidates:
        if not os.path.isdir(root):
            continue
        direct = os.path.join(root, "Microsoft.Web.WebView2.WinForms.dll")
        if os.path.exists(direct):
            return direct
        try:
            names = sorted(os.listdir(root), reverse=True)
        except OSError:
            continue
        for name in names:
            for sub in (os.path.join(root, name), os.path.join(root, name, "build", "native", "release")):
                candidate = os.path.join(sub, "Microsoft.Web.WebView2.WinForms.dll")
                if os.path.exists(candidate):
                    return candidate
    return ""


def load_dotnet():
    """加载 .NET 运行时和 WindowsDesktop 程序集，返回模块字典。

    失败会抛异常，由调用方决定兜底。
    """
    import clr  # noqa: F401  (导入即初始化 CLR)
    import System
    from System import AppDomain, Enum, ResolveEventHandler, Reflection, Environment, Type, Int32

    res_dir, ver = pick_windesktop_runtime()
    if not res_dir:
        raise RuntimeError("没有找到 Microsoft.WindowsDesktop.App 运行时，无法开窗口")
    log_line(f"使用 WindowsDesktop {ver}：{res_dir}")

    # 关键一步：让 CLR 在自己找不到程序集时去 WindowsDesktop 目录里捞。
    def _resolve(sender, args):
        try:
            name = str(args.Name).split(",")[0].strip()
            path = os.path.join(res_dir, name + ".dll")
            if os.path.exists(path):
                return Reflection.Assembly.LoadFrom(path)
        except Exception:  # noqa: BLE001
            pass
        return None

    AppDomain.CurrentDomain.AssemblyResolve += ResolveEventHandler(_resolve)

    forms = Reflection.Assembly.LoadFrom(os.path.join(res_dir, "System.Windows.Forms.dll"))
    # 注意：System.Drawing.dll 是外观程序集，LoadFrom 会撞上已加载的同名引用（0x80131040）。
    # 形状/颜色/字体类型都在 System.Drawing.Common.dll 里，必须直接加载它。
    drawing = from_path_load(os.path.join(res_dir, "System.Drawing.Common.dll"))

    # pythonnet 3.0.3 通过反射拿到的 System.Drawing.Color 看不到静态方法
    # （hasattr 为 False），所以静态调用改成反射，见 Argb()。
    color_type = drawing.GetType("System.Drawing.Color")
    method_flags = 0x18 | 0x8  # Static | Public | FlattenHierarchy

    def _pick_method(name, argc):
        for m in color_type.GetMethods():
            if str(m.Name) == name and m.GetParameters().Length == argc:
                return m
        return None

    from_argb3 = _pick_method("FromArgb", 3)
    # 浅色文字/深色底全部走反射，避开静态属性绑定不到的问题
    def Argb(r, g, b):
        return from_argb3.Invoke(None, System.Array[System.Object]([Int32(r), Int32(g), Int32(b)]))

    def NamedColor(name, fallback):
        try:
            prop = color_type.GetProperty(name)
            if prop is not None:
                return prop.GetValue(None)
        except Exception:  # noqa: BLE001
            pass
        return fallback

    def EnumVal(enum_type, name):
        """pythonnet 3.0.3 拿不到枚举成员属性，统一走 Enum.Parse。"""
        return Enum.Parse(enum_type, name)

    def StaticVal(type_obj, member):
        """静态属性/字段同样拿不到，走反射。"""
        prop = type_obj.GetProperty(member)
        if prop is not None:
            return prop.GetValue(None)
        field = type_obj.GetField(member)
        if field is not None:
            return field.GetValue(None)
        raise AttributeError(f"{type_obj}.{member} 取不到")
    modules = {
        "System": System,
        "forms": forms,
        "drawing": drawing,
        "Type": Type,
        "Environment": Environment,
        "Activator": System.Activator,
        "res_dir": res_dir,
        "Argb": Argb,
        "NamedColor": NamedColor,
        "EnumVal": EnumVal,
        "StaticVal": StaticVal,
    }
    return modules


def show_native_message(text, title=TITLE):
    """纯净版兜底提示：不依赖 WinForms，直接用系统对话框。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 窗口

class _ActionWrapper:
    """把 py 函数包成 Control.BeginInvoke 能吃的委托。

    pythonnet 只能把实现 __call__ 的对象推断成委托，不能直接塞函数；
    参数个数决定委托签名（BeginInvoke 要无参 Action）。
    """

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)

class LauncherWindow:
    def __init__(self, url, dotnet):
        self.url = url
        self.dotnet = dotnet
        self.form = None
        self.webview = None
        self._pending_ready = None
        self._ready_timer = None

    # -- 位置记忆
    def _saved_geometry(self):
        data = read_json(WINDOW_STATE, {})
        try:
            w = int(data.get("width") or DEFAULT_SIZE[0])
            h = int(data.get("height") or DEFAULT_SIZE[1])
        except (TypeError, ValueError):
            w, h = DEFAULT_SIZE
        return {
            "width": max(MIN_SIZE[0], w),
            "height": max(MIN_SIZE[1], h),
            "x": data.get("x"),
            "y": data.get("y"),
        }

    def _remember_geometry(self):
        if not self.form:
            return
        try:
            bounds = self.form.Bounds
            write_json(WINDOW_STATE, {
                "x": int(bounds.X), "y": int(bounds.Y),
                "width": int(bounds.Width), "height": int(bounds.Height),
            })
        except Exception as exc:  # noqa: BLE001
            log_line(f"保存窗口位置失败：{exc}")

    # -- 构建
    def build(self):
        forms = self.dotnet["forms"]
        drawing = self.dotnet["drawing"]
        Form = forms.GetType("System.Windows.Forms.Form")
        Application = forms.GetType("System.Windows.Forms.Application")
        Screen = forms.GetType("System.Windows.Forms.Screen")
        StartPosition = forms.GetType("System.Windows.Forms.FormStartPosition")
        Size = drawing.GetType("System.Drawing.Size")
        Font = drawing.GetType("System.Drawing.Font")
        Panel = forms.GetType("System.Windows.Forms.Panel")
        Label = forms.GetType("System.Windows.Forms.Label")
        DockStyle = forms.GetType("System.Windows.Forms.DockStyle")

        from System import Activator, Array, Boolean, Int32, Object, String
        Argb = self.dotnet["Argb"]

        self.Application = Application
        form = Activator.CreateInstance(Form)
        form.Text = TITLE
        form.MinimumSize = Activator.CreateInstance(Size, Int32(MIN_SIZE[0]), Int32(MIN_SIZE[1]))
        form.BackColor = Argb(24, 26, 31)
        form.ForeColor = Argb(226, 228, 234)
        try:
            form.Font = Activator.CreateInstance(Font, String("Microsoft YaHei UI"), 10.0)
        except Exception:  # noqa: BLE001
            pass

        geo = self._saved_geometry()
        form.Size = Activator.CreateInstance(Size, Int32(geo["width"]), Int32(geo["height"]))
        placed = False
        if geo["x"] is not None and geo["y"] is not None:
            try:
                Point = drawing.GetType("System.Drawing.Point")
                pt = Activator.CreateInstance(Point, Int32(int(geo["x"])), Int32(int(geo["y"])))
                # 位置必须在某块屏幕的可见区域里，防止换显示器后窗口跑到屏幕外
                for screen in self.dotnet["StaticVal"](Screen, "AllScreens"):
                    if screen.WorkingArea.Contains(pt):
                        form.StartPosition = self.dotnet["EnumVal"](StartPosition, "Manual")
                        form.Location = pt
                        placed = True
                        break
            except Exception:  # noqa: BLE001
                placed = False
        if not placed:
            form.StartPosition = self.dotnet["EnumVal"](StartPosition, "CenterScreen")

        # 中间放 WebView2，失败时换成提示面板
        self.panel = Activator.CreateInstance(Panel)
        self.panel.Dock = self.dotnet["EnumVal"](DockStyle, "Fill")
        self.panel.BackColor = Argb(24, 26, 31)
        form.Controls.Add(self.panel)

        self.form = form
        self._center_label = Label
        return form

    def attach_webview(self):
        """在窗体句柄建好后挂 WebView2；返回 True 表示成功。"""
        try:
            from System import Activator, Boolean, Int32, Object, String, Type
            drawing = self.dotnet["drawing"]
            DockStyle = self.dotnet["forms"].GetType("System.Windows.Forms.DockStyle")
            dll = find_webview2_dll()
            if not dll:
                raise RuntimeError("系统里没有找到 WebView2 组件")
            assembly = from_path_load(dll)
            core = assembly.GetType("Microsoft.Web.WebView2.WinForms.WebView2")
            wv = Activator.CreateInstance(core)
            wv.Dock = self.dotnet["EnumVal"](DockStyle, "Fill")
            self.panel.Controls.Add(wv)
            self.webview = wv
            # EnsureCoreWebView2Async 是异步的。不能在 UI 线程上 Wait：初始化依赖
            # UI 消息泵，等下去必然超时；pythonnet 又传不进 Action<Task> 续接委托。
            # 所以：后台线程只轮询任务状态并存结果，UI 线程用 Timer 取走并导航。
            task = wv.EnsureCoreWebView2Async(None)
            self._watch_webview_task(task)
            self._start_ready_timer()
            log_line("WebView2 初始化已发起，等待内核就绪")
            return True
        except Exception as exc:  # noqa: BLE001
            log_line(f"WebView2 不可用：{exc}")
            self._show_fallback_panel(str(exc))
            return False

    def _start_ready_timer(self):
        """UI 线程上的 200ms 定时器：拿到内核后导航并自我注销。"""
        forms = self.dotnet["forms"]
        activator = self.dotnet["Activator"]
        Timer = forms.GetType("System.Windows.Forms.Timer")
        timer = activator.CreateInstance(Timer)
        timer.Interval = 200

        def on_tick(sender, args):
            pending = self._pending_ready
            if pending is None:
                return
            self._pending_ready = None
            sender.Stop()
            self._ready_timer = None
            self._after_webview_ready(pending[0], pending[1])

        timer.Tick += on_tick
        timer.Start()
        self._ready_timer = timer

    @staticmethod
    def _pick_method(obj, name, argc):
        for m in obj.GetType().GetMethods():
            if str(m.Name) == name and m.GetParameters().Length == argc:
                return m
        raise RuntimeError(f"找不到 {name}({argc} 参)")

    def _watch_webview_task(self, task):
        def worker():
            for _ in range(300):  # 最多 60 秒
                try:
                    if bool(task.IsCompleted):
                        break
                except Exception:  # noqa: BLE001
                    break
                time.sleep(0.2)
            ok = False
            try:
                ok = not bool(task.IsFaulted) and not bool(task.IsCanceled)
            except Exception as exc:  # noqa: BLE001
                log_line(f"WebView2 任务异常：{exc}")
            # 注意：CoreWebView2 只能在 UI 线程读，这里只存 "完成与否"，
            # 内核对象由 UI 线程的定时器自己取。
            self._pending_ready = (ok, None)

        threading.Thread(target=worker, name="webview-ready", daemon=True).start()

    def _after_webview_ready(self, ok, core):
        if ok and core is None:
            # 任务完成即在 UI 线程上取一次内核对象
            try:
                core = self.webview.CoreWebView2
            except Exception as exc:  # noqa: BLE001
                log_line(f"取 WebView2 内核失败：{exc}")
                core = None
        if not ok or core is None:
            log_line("WebView2 初始化失败：内核没有就绪")
            self._show_fallback_panel("WebView2 内核初始化失败")
            return
        try:
            core.Navigate(self.url)
            core.NewWindowRequested += self._on_new_window
            log_line("WebView2 已就绪，开始加载控制页")
        except Exception as exc:  # noqa: BLE001
            log_line(f"WebView2 导航失败：{exc}")
            self._show_fallback_panel(str(exc))
    def _on_new_window(self, sender, args):
        try:
            os.startfile(str(args.Uri))
            args.Handled = True
        except Exception as exc:  # noqa: BLE001
            log_line(f"打开外部链接失败：{exc}")

    def _show_fallback_panel(self, reason):
        """没有 WebView2 时的兜底：告诉用户手工打开控制台网址。"""
        try:
            from System import Activator, Int32, String
            forms = self.dotnet["forms"]
            Label = forms.GetType("System.Windows.Forms.Label")
            DockStyle = forms.GetType("System.Windows.Forms.DockStyle")
            drawing = self.dotnet["drawing"]
            Font = drawing.GetType("System.Drawing.Font")
            label = Activator.CreateInstance(Label)
            label.Dock = self.dotnet["EnumVal"](DockStyle, "Fill")
            label.TextAlign = self.dotnet["EnumVal"](drawing.GetType("System.Drawing.ContentAlignment"), "MiddleCenter")
            label.ForeColor = self.dotnet["Argb"](226, 228, 234)
            try:
                label.Font = Activator.CreateInstance(Font, String("Microsoft YaHei UI"), 12.0)
            except Exception:  # noqa: BLE001
                pass
            label.Text = (
                "启动器已在后台运行，但窗口组件没有加载成功。\n"
                f"原因：{reason}\n\n"
                "请用浏览器打开：" + self.url
            )
            self.panel.Controls.Add(label)
        except Exception as exc:  # noqa: BLE001
            log_line(f"兜底面板也失败了：{exc}")

    def run(self):
        form = self.form
        # 句柄建好之后再挂 WebView2，否则容器还没创建
        ready = {"done": False}

        def on_shown(sender, args):
            if ready["done"]:
                return
            ready["done"] = True
            self.attach_webview()

        form.Shown += on_shown
        form.FormClosing += self._on_closing
        # Application.Run 也是静态方法，pythonnet 绑不到，走反射
        Run = None
        for m in self.Application.GetMethods():
            if str(m.Name) == "Run" and m.GetParameters().Length == 1:
                Run = m
                break
        if Run is None:
            raise RuntimeError("找不到 Application.Run(Form)")
        import System as ClrSystem
        Run.Invoke(None, ClrSystem.Array[ClrSystem.Object]([form]))

    def _on_closing(self, sender, args):
        self._remember_geometry()
        log_line("窗口已关闭")


def from_path_load(dll):
    import System
    return System.Reflection.Assembly.LoadFrom(dll)


def run_window(url):
    """打开主窗口；返回退出码。失败时抛异常，由 main 兜底。"""
    dotnet = load_dotnet()
    window = LauncherWindow(url, dotnet)
    window.build()
    window.run()
    return 0


def main(argv):
    url = argv[1] if len(argv) > 1 else "http://127.0.0.1:3100"
    try:
        return run_window(url)
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        log_line("窗口启动失败：\n" + detail)
        show_native_message(
            "启动器窗口没能打开。\n"
            "请用浏览器临时访问：" + url + "\n"
            "（详细原因已记录到 logs\\launcher.log）"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))