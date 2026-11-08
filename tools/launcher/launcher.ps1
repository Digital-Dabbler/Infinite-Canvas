<#
.SYNOPSIS
  Infinite Canvas 启动器菜单（由根目录「启动服务.bat」在无参数、无 IC_NO_BROWSER 时调用）。

.DESCRIPTION
  本脚本只负责交互菜单与状态展示；启动 / 重启 / 停止全部委托
  tools\daily-restart\restart-server.ps1 的 -Action 参数，避免两处实现漂移。
  状态展示沿用了重启脚本同样的只读手法：按端口找 PID、Get-Process 校验归属，
  禁止 taskkill / tasklist / WMI（本机实测会卡死）。本脚本自身绝不结束任何进程。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\launcher\launcher.ps1
#>
[CmdletBinding()]
param(
    [int] $Port = 3000
)

$ErrorActionPreference = 'Continue'
try { $Host.UI.RawUI.WindowTitle = 'Infinite Canvas 启动器' } catch {}

# tools\launcher\ -> 仓库根
$BaseDir    = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RestartPs1 = Join-Path $BaseDir 'tools\daily-restart\restart-server.ps1'
$LogFile    = Join-Path $BaseDir 'logs\daily_restart.log'

# 读监听端口的进程 PID（与 restart-server.ps1 同款：Get-NetTCPConnection 优先，netstat 兜底）。
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

# 只有本项目的解释器/目录下的进程才视为"我们的服务"。
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

function Get-LanUrl {
    $ip = '127.0.0.1'
    try {
        $found = (Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress
        if (-not $found) {
            $found = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress)
        }
        if ($found) { $ip = $found }
    } catch {}
    return ('http://{0}:{1}/' -f $ip, $Port)
}

# 返回 $true 表示本项目服务正在监听。
function Show-Status {
    $pids = @(Get-ListenPid -LocalPort $Port)
    if ($pids.Count -eq 0) {
        Write-Host ('  状态: 未运行（端口 {0} 空闲）' -f $Port) -ForegroundColor Yellow
        return $false
    }
    $ours = 0
    foreach ($p in $pids) {
        $info = Get-ProcessInfo $p
        if ($info.Exists -and (Test-ProjectProcess $info.Path)) { $ours++ }
    }
    if ($ours -eq $pids.Count) {
        Write-Host ('  状态: 运行中（PID {0}，端口 {1}）' -f ($pids -join ','), $Port) -ForegroundColor Green
        return $true
    }
    Write-Host ('  状态: 端口 {0} 被其它程序占用（PID {1}），本项目服务未运行' -f $Port, ($pids -join ',')) -ForegroundColor Red
    return $false
}

function Invoke-RestartAction {
    param([string] $Action, [switch] $NoBrowser)
    if (-not (Test-Path -LiteralPath $RestartPs1)) {
        Write-Host ('  [ERROR] 缺少 {0}' -f $RestartPs1) -ForegroundColor Red
        return 1
    }
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $RestartPs1, '-Action', $Action, '-Port', $Port)
    if ($NoBrowser) { $psArgs += '-NoBrowser' }
    & powershell.exe @psArgs
    return $LASTEXITCODE
}

function Wait-Back {
    Write-Host ''
    Read-Host '  按回车返回菜单' | Out-Null
}

Clear-Host
while ($true) {
    Write-Host '===== Infinite Canvas 启动器 =====' -ForegroundColor Cyan
    $running = Show-Status
    Write-Host ('  本机:   http://127.0.0.1:{0}/' -f $Port)
    Write-Host ('  局域网: {0}' -f (Get-LanUrl))
    Write-Host ''
    Write-Host '  [1] 启动服务（新窗口运行，自动打开网页）'
    Write-Host '  [2] 重启服务（静默重启，完成后刷新页面即可）'
    Write-Host '  [3] 停止服务'
    Write-Host '  [4] 打开网页'
    Write-Host '  [0] 退出启动器'
    Write-Host ''
    $choice = Read-Host '  请选择'
    switch ($choice) {
        '1' {
            if ($running) {
                Write-Host '  服务已在运行，无需重复启动。' -ForegroundColor Yellow
            } else {
                $code = Invoke-RestartAction -Action Start
                if ($code -eq 0) {
                    Write-Host '  启动完成。' -ForegroundColor Green
                } elseif ($code -eq 2) {
                    Write-Host '  端口被非本项目程序占用，已中止（避免误杀）。' -ForegroundColor Red
                } else {
                    Write-Host ('  启动失败，详见日志: {0}' -f $LogFile) -ForegroundColor Red
                }
            }
            Wait-Back
        }
        '2' {
            $code = Invoke-RestartAction -Action Restart -NoBrowser
            if ($code -eq 0) {
                Write-Host '  重启完成，刷新浏览器页面即可。' -ForegroundColor Green
            } elseif ($code -eq 2) {
                Write-Host '  端口被非本项目程序占用，已中止（避免误杀）。' -ForegroundColor Red
            } else {
                Write-Host ('  重启失败，详见日志: {0}' -f $LogFile) -ForegroundColor Red
            }
            Wait-Back
        }
        '3' {
            $code = Invoke-RestartAction -Action Stop
            if ($code -eq 0) {
                Write-Host '  已停止。' -ForegroundColor Green
            } elseif ($code -eq 2) {
                Write-Host '  端口被非本项目程序占用，本脚本不会结束它。' -ForegroundColor Red
            } else {
                Write-Host ('  停止失败，详见日志: {0}' -f $LogFile) -ForegroundColor Red
            }
            Wait-Back
        }
        '4' {
            Start-Process (Get-LanUrl)
        }
        '0' {
            Write-Host '  再见。'
            exit 0
        }
        default {
            Write-Host '  无效选项，请输入 0-4。' -ForegroundColor Yellow
            Start-Sleep -Milliseconds 800
        }
    }
    Clear-Host
}
