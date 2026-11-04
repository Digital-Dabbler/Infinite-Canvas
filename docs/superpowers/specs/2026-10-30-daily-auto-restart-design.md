# Daily 06:00 Automatic Restart

## Problem

这台机器上的 Infinite Canvas 是一个长期运行的本机服务：`启动服务.bat` 在前台执行
`python\python.exe main.py`，绑定 `0.0.0.0:3000`，没有热重载，也没有服务化封装。用户希望它
**每天早上 6:00 自动重启一次**，用来清掉一天累积的内存/缓存与偶发卡死，而不用手点。

现状里只有一条类似的机制：`main.py` 的 `schedule_self_restart(delay_seconds=3)`
（`main.py:3546`，供管理员「更新后自动重启」调用）会派生 `_self_restart.bat`，延迟数秒后
`taskkill` 掉自己的 PID，再用
`start "ComfyUI-API-Modelscope" /D "<APP_DIR>" cmd /k call "启动服务.bat"` 重新拉起，日志写
`_self_restart.log`。它有两个问题，使它不能直接当作「每天 6 点」用：

1. **它由进程自己驱动。** 定时器在 `main.py` 的事件循环里，一旦进程卡死或半挂，定时器本身
   也不会触发——而这正是最需要重启的场合。
2. **它只能被动等待管理员点按钮。** 没有时间表达，也没有任何时间表可配置。

因此本设计把「时间表」交给操作系统，把「怎么重启」做成仓库里可复用的脚本。

## Constraints discovered

- **WMI 在这台机器上会卡死。** `Get-CimInstance Win32_Process`（即便按 `ProcessId` 过滤）
  实测超过 120 秒不返回，所以重启脚本**不能**用 `Get-CimInstance` / `wmic` 去找进程或父进程。
  可用的快速事实来源是 `Get-NetTCPConnection -LocalPort 3000`（毫秒级）和 `Get-Process -Id`。
- **不能按进程名杀 python。** 同一台机器上还跑着 ComfyUI（`standalone-env\python.exe`、
  `.venv\Scripts\python.exe`），`taskkill /IM python.exe` 会连它们一起杀掉。
- `schtasks` 在普通权限（Medium Mandatory Level）下即可创建「仅当前用户、交互式登录」的
  计划任务，无需管理员提权。
- 服务是单进程 uvicorn（`main.py:29211` 的 `uvicorn.run`，无 reload），杀掉一个 PID 就是停机。
- 前端已有重启感知：`smart-canvas.js` 对比 `meta.boot_id`，变化即弹「服务已重启，请刷新」横幅，
  所以定时重启不需要前端配合。
- 服务端 `recover_canvas_tasks_after_restart()`（`main.py:5996`）会在启动时恢复画布任务的
  持久化状态，重启不会丢任务记录（进行中的上游请求会中断）。

## Design

### 组成

| 文件 | 职责 |
| --- | --- |
| `tools/daily-restart/restart-server.ps1` | 重启核心。找到监听 3000 端口的 PID → 校验它属于本项目 → 结束它 → 等端口释放 → 用 `启动服务.bat` 在新窗口拉起 → 轮询健康检查 → 失败重试 → 追加日志。支持 `-DryRun`。 |
| `每日重启服务.bat` | 手动一键重启，调用上面的 ps1，输出结果后 `pause`，方便用户随时手动执行同一套逻辑。 |
| `安装每日重启任务.bat` | 幂等注册计划任务 `InfiniteCanvas_DailyRestart`（每天 06:00、当前用户、交互式、`StartWhenAvailable`）。重复双击只更新，不重复创建。 |
| `卸载每日重启任务.bat` | 删除该计划任务；脚本与日志保留。 |
| `tools/daily-restart/run-hidden.vbs` | 计划任务的实际动作：用 `WScript.Shell.Run(cmd, 0, False)` 隐藏窗口启动 ps1，避免 06:00 闪一个黑窗。 |
| `启动服务.bat`（改） | 加 `title Infinite Canvas` 作为窗口标识；浏览器启动改为 `if not defined IC_NO_BROWSER` 才执行——手动双击行为不变，定时重启静默不弹浏览器。 |
| `logs/daily_restart.log` | 运行日志（运行时产生，加入 `.gitignore`，不提交）。 |

### 重启流程（`restart-server.ps1`）

1. **定位**：`Get-NetTCPConnection -LocalPort $Port -State Listen` 取 `OwningProcess`；
   该命令不可用时回退解析 `netstat -ano`。没有监听者表示服务本来就没跑，跳过结束步骤直接启动。
2. **校验**：用 `Get-Process -Id` 取 `.Path`，必须等于 `%BaseDir%\python\python.exe` 或以
   `%BaseDir%` 开头；否则判定为「3000 被其他程序占用」，**不杀**、记错误日志、退出码 2。
   这是防止误杀 ComfyUI 等其它 python 的唯一一道闸。
3. **结束**：`Stop-Process -Id <pid> -Force`，随后 10 秒内轮询确认进程消失，再按
   `-WaitPortSeconds`（默认 30 秒）轮询端口释放。**不使用 `taskkill`**：本机 `taskkill`
   （含 `/T`）会卡住 60~120 秒并返回失败，`tasklist /v` 同样卡死，只有 `Stop-Process`
   是毫秒级且可靠。
4. **启动**：`Start-Process cmd.exe '/c call "<BaseDir>\启动服务.bat"'`，`WorkingDirectory` 为
   仓库根；启动前设置 `$env:IC_NO_BROWSER = '1'`（`-NoBrowser` 时），子进程继承该变量。
   用 `/c` 而不是 `/k`：批处理结束时自己的控制台标签页随之关闭。配合 `启动服务.bat` 结尾
   `if not defined IC_NO_BROWSER pause`，自动重启出来的实例在第二天被结束时标签页自动关闭，
   手动双击启动的实例仍然停在 `pause` 上供人看清错误。这样既不需要按窗口标题找旧窗口，也不会
   每天累积一个「请按任意键继续」的窗口。
5. **健康检查**：按 `-HealthTimeoutSeconds`（默认 120 秒）轮询 `http://127.0.0.1:<Port>/`，
   拿到任意 HTTP 响应即视为起来（未登录会 302 到登录页，也证明服务活着）。失败按 `-Retries`
   （默认 1）重新结束+启动一次。
6. **日志**：全程用 `[System.IO.File]::AppendAllText(utf8-nobom)` 追加到
   `logs\daily_restart.log`，每行带 `yyyy-MM-dd HH:mm:ss` 前缀；同时写控制台，便于手动运行时直接看。

退出码：`0` 成功 / `1` 参数或环境错误 / `2` 端口被非本项目进程占用（拒绝操作）/ `3` 重启后
健康检查未通过。

### 计划任务

任务名 `InfiniteCanvas_DailyRestart`。触发器 `CalendarTrigger`，`StartBoundary` 取安装当天
06:00，`ScheduleByDay/DaysInterval=1`；`Settings` 里 `StartWhenAvailable=true`（6:00 时机器
休眠/关机，唤醒或开机后尽快补跑一次）、`MultipleInstancesPolicy=IgnoreNew`、`ExecutionTimeLimit=PT1H`；
`Principal` 为当前用户、`LogonType=InteractiveToken`、`RunLevel=Limited`（`New-ScheduledTaskPrincipal`
只接受 `Limited`/`Highest`，写成 `LeastPrivilege` 会报枚举转换失败；必须交互式登录，
否则新起的服务窗口会落在无桌面会话里，用户看不到也 Ctrl+C 不了）。

任务动作：`wscript.exe "<BaseDir>\tools\daily-restart\run-hidden.vbs"`。

### 边界与失败姿态

- 6:00 若有人正在使用或任务在跑：按用户选择直接重启。进行中的上游生成请求会中断，画布任务
  记录由启动恢复逻辑收敛；前端提示刷新。
- 端口被外人占用时不硬来：不杀、不重复启动（重复启动只会因端口冲突失败），留错误日志给人判断。
- 脚本本身不依赖网络、不依赖管理员权限、不写入仓库以外的路径。
- 卸载任务不删脚本，随时可重新安装；换目录后需重跑安装脚本（任务里存的是绝对路径）。

## Testing

- `tests/test_daily_restart.py`：静态契约（任务名/06:00/`IC_NO_BROWSER`/无 WMI 调用/端口校验/
  禁止 `taskkill`+`tasklist`/`cmd /c` 与受守卫的 `pause`）+ 真实 `-DryRun` 子进程校验
  （不杀进程、退出码 0、能正确识别当前监听者）。
- 人工验收：安装任务后 `schtasks /query` 确认下次运行时间为次日 06:00；手动执行一次
  `每日重启服务.bat` 走完「结束→启动→健康检查」并确认 3000 端口恢复、页面可打开。

## 本机实测（2026-10-30）

- `schtasks /run` 真实重启两次，均成功：旧 PID 结束 → 新 PID 起、`HTTP 200`、日志以
  `===== 每日重启完成 =====` 收尾。改用 `Stop-Process` 后「结束」耗时从 61 秒降到 3 秒。
- 本机会卡死的命令：`Get-CimInstance Win32_Process`（>120s）、`taskkill`（含 `/T`，60~120s
  后返回失败）、`tasklist /v`（>120s）。可用：`Get-NetTCPConnection`、`Get-Process`、`netstat -ano`。
- 本机默认终端是 Windows Terminal：服务窗口由 `WindowsTerminal.exe`/`OpenConsole.exe` 持有，
  `Get-Process cmd` 的 `MainWindowTitle` 恒为空，且多个 cmd 共用一个终端窗口的不同标签页。
  因此任何「按 cmd 窗口标题找旧实例」的做法都不可靠，最终改为上面的 `cmd /c` + 受守卫 `pause`。
- 健康检查实测 18~52 秒（`启动服务.bat` 里探测局域网 IP 的 PowerShell 与 `main.py` 导入占主要
  时间），`-HealthTimeoutSeconds` 默认 120 秒足够。
