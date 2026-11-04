<#
.SYNOPSIS
  Infinite Canvas 无人值守重启脚本（供每日 06:00 计划任务与「每日重启服务.bat」共用）。

.DESCRIPTION
  1) 通过监听端口（默认 3000）找到服务进程，并校验它确实属于本项目，避免误杀 ComfyUI 等其它 python；
  2) 结束旧进程并等待端口释放；
  3) 关掉旧实例遗留的「Server stopped / 请按任意键继续」窗口；
  4) 用根目录「启动服务.bat」在新控制台窗口拉起服务（默认带 IC_NO_BROWSER=1，静默不弹浏览器）；
  5) 轮询 http://127.0.0.1:<Port>/ 做健康检查，失败按 -Retries 重试；
  6) 全程追加写 logs\daily_restart.log。

  退出码：0 成功 / 1 环境或参数错误 / 2 端口被非本项目进程占用（拒绝操作）/ 3 重启后仍未就绪。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\daily-restart\restart-server.ps1 -NoBrowser

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\daily-restart\restart-server.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [int]    $Port                 = 3000,
    [string] $BaseDir              = '',
    [int]    $WaitPortSeconds      = 30,
    [int]    $HealthTimeoutSeconds = 120,
    [int]    $Retries              = 1,
    [switch] $NoBrowser,
    [switch] $DryRun
)

$ErrorActionPreference = 'Continue'

# 脚本位于 <仓库根>\tools\daily-restart\，默认向上三层找仓库根。
if ([string]::IsNullOrWhiteSpace($BaseDir)) {
    $BaseDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
}
if ([string]::IsNullOrWhiteSpace($BaseDir) -or -not (Test-Path -LiteralPath $BaseDir)) {
    Write-Host "[ERROR] 无法确定项目目录: $BaseDir"
    exit 1
}
$BaseDir  = [System.IO.Path]::GetFullPath($BaseDir).TrimEnd('\')
$LogDir   = Join-Path $BaseDir 'logs'
$LogPath  = Join-Path $LogDir 'daily_restart.log'
$Launcher = Join-Path $BaseDir '启动服务.bat'
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Log {
    param(
        [string] $Message,
        [string] $Level = 'INFO'
    )
    $line = ('[{0}] [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message)
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        [System.IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $script:Utf8NoBom)
    } catch {
        # 日志写不进去不能影响重启本身
    }
    Write-Host $line
}

# 读监听端口的进程 PID。Get-NetTCPConnection 最快；不可用时回退解析 netstat。
function Get-ListenPid {
    param([int] $LocalPort)
    $result = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction Stop
        foreach ($c in @($conns)) {
            if ($c.OwningProcess) { $result += [int]$c.OwningProcess }
        }
    } catch {
        $netstat = & netstat -ano -p TCP 2>$null
        foreach ($line in @($netstat)) {
            if ($line -match '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
                if ([int]$Matches[1] -eq $LocalPort) { $result += [int]$Matches[2] }
            }
        }
    }
    return @($result | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
}

# 进程是否还在，以及它的 exe 路径。不用 WMI：本机 Get-CimInstance 会长时间不返回。
function Get-ProcessInfo {
    param([int] $ProcessId)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return [pscustomobject]@{
            Exists = $true
            Path   = [string]$p.Path
            Name   = [string]$p.ProcessName
        }
    } catch {
        return [pscustomobject]@{ Exists = $false; Path = ''; Name = '' }
    }
}

# 只有本项目的解释器/目录下的进程才允许被结束。
function Test-ProjectProcess {
    param([string] $ExePath)
    if ([string]::IsNullOrWhiteSpace($ExePath)) { return $false }
    try {
        $exe = [System.IO.Path]::GetFullPath($ExePath)
    } catch {
        return $false
    }
    $expected = Join-Path $BaseDir 'python\python.exe'
    if ($exe.Equals([System.IO.Path]::GetFullPath($expected), [StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $exe.StartsWith($BaseDir + '\', [StringComparison]::OrdinalIgnoreCase)
}

# 只用 Stop-Process：这台机器上 taskkill（含 /T）会卡住 60~120 秒并返回失败，tasklist /v 同样卡死。
function Stop-ServerProcess {
    param([int] $ProcessId)
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Write-Log ('已请求结束进程 {0}' -f $ProcessId)
    } catch {
        Write-Log ('结束进程 {0} 失败: {1}' -f $ProcessId, $_.Exception.Message) 'ERROR'
        return $false
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-ProcessInfo $ProcessId).Exists) {
            Write-Log ('进程 {0} 已退出' -f $ProcessId)
            return $true
        }
        Start-Sleep -Milliseconds 300
    }
    Write-Log ('进程 {0} 在 10 秒内仍未退出' -f $ProcessId) 'ERROR'
    return $false
}

function Wait-PortFree {
    param([int] $LocalPort, [int] $Seconds)
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $Seconds))
    while ((Get-Date) -lt $deadline) {
        if (-not @(Get-ListenPid -LocalPort $LocalPort).Count) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return (-not @(Get-ListenPid -LocalPort $LocalPort).Count)
}

function Start-Server {
    if (-not (Test-Path -LiteralPath $Launcher)) {
        Write-Log ('找不到启动脚本: {0}' -f $Launcher) 'ERROR'
        return $false
    }
    if ($NoBrowser) {
        $env:IC_NO_BROWSER = '1'
    } else {
        Remove-Item Env:IC_NO_BROWSER -ErrorAction SilentlyContinue
    }
    # 用 cmd /c：批处理结束时窗口会自己关闭，不会留下停留的黑窗。
    # 自动重启的实例继承 IC_NO_BROWSER=1，启动脚本因此不打开浏览器、退出时也不 pause。
    $cmdArgs = @('/c', ('call "{0}"' -f $Launcher))
    try {
        Start-Process -FilePath $env:ComSpec -ArgumentList $cmdArgs -WorkingDirectory $BaseDir -ErrorAction Stop
        Write-Log ('已在新窗口启动: {0} {1}' -f $env:ComSpec, ($cmdArgs -join ' '))
        return $true
    } catch {
        Write-Log ('启动失败: {0}' -f $_.Exception.Message) 'ERROR'
        return $false
    }
}

# 端口在监听、并且 HTTP 有响应（未登录时会 302 到登录页，同样说明服务活着）。
function Wait-ServerReady {
    param([int] $LocalPort, [int] $Seconds)
    $uri = ('http://127.0.0.1:{0}/' -f $LocalPort)
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $Seconds))
    while ((Get-Date) -lt $deadline) {
        if (@(Get-ListenPid -LocalPort $LocalPort).Count) {
            try {
                $resp = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
                return ('HTTP {0}' -f [int]$resp.StatusCode)
            } catch {
                $code = 0
                try {
                    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                        $code = [int]$_.Exception.Response.StatusCode
                    }
                } catch { $code = 0 }
                if ($code -gt 0) { return ('HTTP {0}' -f $code) }
            }
        }
        Start-Sleep -Milliseconds 800
    }
    return $null
}

Write-Log ('===== 每日重启开始 (DryRun={0}, NoBrowser={1}) =====' -f [bool]$DryRun, [bool]$NoBrowser)
Write-Log ('项目目录: {0}' -f $BaseDir)

if (-not (Test-Path -LiteralPath $Launcher)) {
    Write-Log ('缺少启动脚本: {0}' -f $Launcher) 'ERROR'
    exit 1
}

$listenPids = @(Get-ListenPid -LocalPort $Port)
if ($listenPids.Count -eq 0) {
    Write-Log ('端口 {0} 当前没有监听进程，判定服务未运行，直接启动' -f $Port)
} else {
    foreach ($listenPid in $listenPids) {
        $info = Get-ProcessInfo $listenPid
        if (-not $info.Exists) {
            Write-Log ('端口 {0} 的监听进程 {1} 已消失，跳过' -f $Port, $listenPid)
            continue
        }
        if (-not (Test-ProjectProcess $info.Path)) {
            Write-Log ('端口 {0} 被非本项目进程占用: PID={1} {2} ({3}) —— 为避免误杀不执行重启' -f $Port, $listenPid, $info.Name, $info.Path) 'ERROR'
            exit 2
        }
        Write-Log ('待结束进程: PID={0} {1} ({2})' -f $listenPid, $info.Name, $info.Path)
        if (-not $DryRun) { Stop-ServerProcess $listenPid }
    }
}

if ($DryRun) {
    if ($listenPids.Count -eq 0) {
        Write-Log ('DryRun：将启动 {0}' -f $Launcher)
    } else {
        Write-Log ('DryRun：将结束上面的进程，等待端口释放后用 {0} 重新启动' -f $Launcher)
    }
    Write-Log 'DryRun：未做任何改动'
    exit 0
}

if (-not (Wait-PortFree -LocalPort $Port -Seconds $WaitPortSeconds)) {
    Write-Log ('等待端口 {0} 释放超时（{1} 秒），放弃重启' -f $Port, $WaitPortSeconds) 'ERROR'
    exit 3
}

$attempt = 0
while ($true) {
    $attempt++
    if (-not (Start-Server)) { exit 3 }
    Write-Log ('第 {0} 次启动完成，开始健康检查（最长 {1} 秒）' -f $attempt, $HealthTimeoutSeconds)
    $health = Wait-ServerReady -LocalPort $Port -Seconds $HealthTimeoutSeconds
    if ($health) {
        Write-Log ('服务已就绪: http://127.0.0.1:{0}/ ({1})' -f $Port, $health)
        Write-Log '===== 每日重启完成 ====='
        exit 0
    }
    Write-Log ('健康检查未通过（第 {0} 次启动）' -f $attempt) 'ERROR'
    if ($attempt -gt $Retries) { break }
    # 重试前清掉可能残留的半死进程，避免第二次启动撞端口
    foreach ($listenPid in @(Get-ListenPid -LocalPort $Port)) {
        if (Test-ProjectProcess (Get-ProcessInfo $listenPid).Path) { Stop-ServerProcess $listenPid }
    }
    if (-not (Wait-PortFree -LocalPort $Port -Seconds $WaitPortSeconds)) {
        Write-Log ('重试前端口 {0} 仍未释放' -f $Port) 'ERROR'
        break
    }
}

Write-Log '===== 每日重启失败：服务未能就绪 =====' 'ERROR'
exit 3
