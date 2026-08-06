@echo off
setlocal
chcp 65001 >nul

set "IC_SOURCE_DIR=C:\AI\Infinite-Canvas\tools\photoshop-canvas-bridge\dist"
set "IC_BACKUP_DIR=\\192.168.1.2\美术\2_原画\07_学习软件\AI\无限画布PS插件版本"

echo [INFO] 正在查找最新版 Photoshop 插件 ZIP...

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$sourceDir = $env:IC_SOURCE_DIR;" ^
  "$backupDir = $env:IC_BACKUP_DIR;" ^
  "$namePattern = '^Infinite-Canvas-Photoshop-Bridge-(?<version>\d+(?:\.\d+){1,3})\.zip$';" ^
  "try {" ^
  "  if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) { throw ('源目录不存在：' + $sourceDir) }" ^
  "  $packages = @(Get-ChildItem -LiteralPath $sourceDir -File -Filter 'Infinite-Canvas-Photoshop-Bridge-*.zip' | Where-Object { $_.Name -match $namePattern } | ForEach-Object { [pscustomobject]@{ File = $_; Version = [version]$Matches['version'] } });" ^
  "  if ($packages.Count -eq 0) { throw '未找到带版本号的插件 ZIP（预期名称示例：Infinite-Canvas-Photoshop-Bridge-0.2.4.zip）' }" ^
  "  $latest = ($packages | Sort-Object -Property @{ Expression = { $_.Version }; Descending = $true }, @{ Expression = { $_.File.LastWriteTimeUtc }; Descending = $true } | Select-Object -First 1).File;" ^
  "  if (-not (Test-Path -LiteralPath $backupDir -PathType Container)) { New-Item -ItemType Directory -Path $backupDir -Force | Out-Null }" ^
  "  $destination = Join-Path $backupDir $latest.Name;" ^
  "  Write-Host ('[INFO] 正在备份：' + $latest.Name);" ^
  "  Copy-Item -LiteralPath $latest.FullName -Destination $destination -Force;" ^
  "  $sourceHash = (Get-FileHash -LiteralPath $latest.FullName -Algorithm SHA256).Hash;" ^
  "  $backupHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash;" ^
  "  if ($sourceHash -ne $backupHash) { throw '备份校验失败，源文件与目标文件的 SHA-256 不一致；未清理历史版本。' }" ^
  "  Write-Host '[OK] 备份完成，SHA-256 校验通过。';" ^
  "  $backups = @(Get-ChildItem -LiteralPath $backupDir -File -Filter 'Infinite-Canvas-Photoshop-Bridge-*.zip' | Where-Object { $_.Name -match $namePattern } | ForEach-Object { [pscustomobject]@{ File = $_; Version = [version]$Matches['version'] } } | Sort-Object -Property @{ Expression = { $_.Version }; Descending = $true }, @{ Expression = { $_.File.LastWriteTimeUtc }; Descending = $true });" ^
  "  $expired = @($backups | Select-Object -Skip 5);" ^
  "  foreach ($item in $expired) { Write-Host ('[CLEAN] 删除历史版本：' + $item.File.Name); Remove-Item -LiteralPath $item.File.FullName -Force }" ^
  "  Write-Host ('[DONE] 目标目录现保留最近 ' + [Math]::Min(5, $backups.Count) + ' 个版本。');" ^
  "  exit 0;" ^
  "} catch {" ^
  "  Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red;" ^
  "  exit 1;" ^
  "}"

set "IC_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%IC_EXIT_CODE%"=="0" (
    echo 备份失败，请检查上方错误、网络连接及共享目录权限。
) else (
    echo 操作成功。
)
pause
exit /b %IC_EXIT_CODE%
