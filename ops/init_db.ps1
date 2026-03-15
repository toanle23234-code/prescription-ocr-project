param(
    [switch]$Reset,
    [switch]$SeedAdmin,
    [string]$AdminFullname = "System Admin",
    [string]$AdminEmail = "admin@example.com",
    [string]$AdminPassword = "Admin@123"
)

$ErrorActionPreference = "Stop"

$cmd = "python ops/init_db.py"
if ($Reset) {
    $cmd = "$cmd --reset"
}

if ($SeedAdmin) {
    $cmd = "$cmd --seed-admin --admin-fullname \"$AdminFullname\" --admin-email \"$AdminEmail\" --admin-password \"$AdminPassword\""
}

Write-Host "[db-init] Running: $cmd"
Invoke-Expression $cmd
