param(
    [int]$Count = 12
)

$ErrorActionPreference = "Stop"

$cmd = "python ops/seed_sqlserver_users.py --count $Count"
Write-Host "[sqlserver-seed] Running: $cmd"
Invoke-Expression $cmd
