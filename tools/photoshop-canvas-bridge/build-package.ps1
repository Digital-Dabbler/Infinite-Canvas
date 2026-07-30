$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dist = Join-Path $ProjectRoot "dist"
$Stage = Join-Path $env:TEMP ("infinite-canvas-bridge-" + [guid]::NewGuid().ToString("N"))
$Bundle = Join-Path $Stage "Infinite-Canvas-Photoshop-Bridge"
$Version = ([regex]::Match(
    (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot "CSXS\manifest.xml")),
    'ExtensionBundleVersion="([^"]+)"'
)).Groups[1].Value
if (-not $Version) { throw "Cannot read ExtensionBundleVersion from CSXS\manifest.xml" }
$Zip = Join-Path $Dist ("Infinite-Canvas-Photoshop-Bridge-" + $Version + ".zip")
$LatestZip = Join-Path $Dist "Infinite-Canvas-Photoshop-Bridge.zip"

New-Item -ItemType Directory -Force -Path $Bundle | Out-Null
Get-ChildItem -LiteralPath $ProjectRoot -Force |
    Where-Object { $_.Name -ne "dist" } |
    Copy-Item -Destination $Bundle -Recurse -Force
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -LiteralPath $Bundle -DestinationPath $Zip -CompressionLevel Optimal
Remove-Item -LiteralPath $Stage -Recurse -Force
Write-Host "Created $Zip"
try {
    Copy-Item -LiteralPath $Zip -Destination $LatestZip -Force -ErrorAction Stop
    Write-Host "Updated $LatestZip"
} catch {
    Write-Warning "The unversioned ZIP is open in another program. Use $Zip instead."
}
