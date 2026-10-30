<#
.SYNOPSIS
  删除「每天 06:00 自动重启 Infinite Canvas」计划任务（脚本与日志保留）。
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'InfiniteCanvas_DailyRestart'
)

$ErrorActionPreference = 'Continue'

$task = $null
try { $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { $task = $null }

if (-not $task) {
    Write-Host "计划任务不存在，无需卸载: $TaskName" -ForegroundColor Yellow
    exit 0
}

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
} catch {
    & schtasks /delete /tn $TaskName /f 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("[ERROR] 删除计划任务失败（退出码 {0}）" -f $LASTEXITCODE) -ForegroundColor Red
        exit 1
    }
}

Write-Host "已删除计划任务: $TaskName" -ForegroundColor Green
Write-Host "重启脚本与日志仍保留：每日重启服务.bat / tools\daily-restart\ / logs\daily_restart.log"
exit 0
