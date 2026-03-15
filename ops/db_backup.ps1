param(
    [string]$OutDir = "database/backups"
)

$ErrorActionPreference = "Stop"

$dbPath = "database/app.db"
if (-not (Test-Path $dbPath)) {
    throw "Database not found: $dbPath"
}

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $OutDir "app_$stamp.db"
Copy-Item -Path $dbPath -Destination $outFile -Force

Write-Host "[db-backup] Created: $outFile"
