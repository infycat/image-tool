$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

python -m PyInstaller --noconfirm --clean .\batch_bg_remover.spec

Write-Host ""
Write-Host "Build complete: $projectRoot\dist\batch_bg_remover"

