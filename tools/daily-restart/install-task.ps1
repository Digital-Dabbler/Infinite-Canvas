<#
.SYNOPSIS
  注册 / 更新「每天 06:00 自动重启 Infinite Canvas」的 Windows 计划任务。

.DESCRIPTION
  任务名默认 InfiniteCanvas_DailyRestart，动作是 wscript 隐藏窗口调用 restart-server.ps1，
  仅当前用户交互式登录时运行（新起的服务窗口必须落在用户桌面会话里），
  StartWhenAvailable 保证 06:00 时机器休眠/关机错过之后，唤醒或开机时尽快补跑一次。
  重复执行是安全的：同名任务会被覆盖更新。
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'InfiniteCanvas_DailyRestart',
    [string] $Time     = '06:00',
    [string] $BaseDir  = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($BaseDir)) {
    $BaseDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
}
if ([string]::IsNullOrWhiteSpace($BaseDir) -or -not (Test-Path -LiteralPath $BaseDir)) {
    Write-Host "[ERROR] 无法确定项目目录: $BaseDir" -ForegroundColor Red
    exit 1
}
$BaseDir = [System.IO.Path]::GetFullPath($BaseDir).TrimEnd('\')

$Vbs = Join-Path $BaseDir 'tools\daily-restart\run-hidden.vbs'
$Ps1 = Join-Path $BaseDir 'tools\daily-restart\restart-server.ps1'
foreach ($required in @($Vbs, $Ps1)) {
    if (-not (Test-Path -LiteralPath $required)) {
        Write-Host "[ERROR] 缺少文件: $required" -ForegroundColor Red
        exit 1
    }
}

Write-Host "任务名: $TaskName"
Write-Host "触发时间: 每天 $Time"
Write-Host "项目目录: $BaseDir"

$registered = $false
try {
    $at = [datetime]::ParseExact($Time, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $Vbs) -WorkingDirectory $BaseDir
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    $principal = New-ScheduledTaskPrincipal `
        -UserId ('{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME) `
        -LogonType Interactive `
        -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force `
        -Description '每天 06:00 自动重启 Infinite Canvas 服务（tools/daily-restart）' | Out-Null
    $registered = $true
} catch {
    Write-Host ("[WARN] ScheduledTasks 模块注册失败，回退 schtasks：{0}" -f $_.Exception.Message) -ForegroundColor Yellow
}

if (-not $registered) {
    & schtasks /create /tn $TaskName /tr ('wscript.exe "{0}"' -f $Vbs) /sc daily /st $Time /f 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 计划任务创建失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red
        exit 1
    }
}

try {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $next = $info.NextRunTime
    Write-Host ""
    Write-Host "已安装计划任务: $TaskName" -ForegroundColor Green
    Write-Host ("下次运行时间: {0}" -f $next)
    Write-Host "日志文件: $(Join-Path $BaseDir 'logs\daily_restart.log')"
    Write-Host ""
    Write-Host "提示：若本机 06:00 处于休眠/关机，任务会在唤醒或开机后尽快补跑一次。"
    Write-Host "      手动立即重启请双击「每日重启服务.bat」；取消自动化请双击「卸载每日重启任务.bat」。"
} catch {
    & schtasks /query /tn $TaskName /fo LIST /v 2>&1 | Out-Host
}

exit 0
