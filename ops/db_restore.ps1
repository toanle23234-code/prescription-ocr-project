param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile
)

$ErrorActionPreference = "Stop"

$dbPath = "database/app.db"
if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

if (-not (Test-Path "database")) {
    New-Item -ItemType Directory -Path "database" | Out-Null
}

if (Test-Path $dbPath) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $safety = "database/app_before_restore_$stamp.db"
    Copy-Item -Path $dbPath -Destination $safety -Force
    Write-Host "[db-restore] Safety copy created: $safety"
}

Copy-Item -Path $BackupFile -Destination $dbPath -Force
Write-Host "[db-restore] Restored database from: $BackupFile"
