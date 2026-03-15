param(
    [switch]$SkipSchema,
    [switch]$SeedAdmin,
    [string]$AdminFullname = "System Admin",
    [string]$AdminEmail = "admin@example.com",
    [string]$AdminPassword = "Admin@123"
)

$ErrorActionPreference = "Stop"

$cmd = "python ops/init_db_sqlserver.py"
if ($SkipSchema) {
    $cmd = "$cmd --skip-schema"
}

if ($SeedAdmin) {
    $cmd = "$cmd --seed-admin --admin-fullname \"$AdminFullname\" --admin-email \"$AdminEmail\" --admin-password \"$AdminPassword\""
}

Write-Host "[sqlserver-db-init] Running: $cmd"
Invoke-Expression $cmd
