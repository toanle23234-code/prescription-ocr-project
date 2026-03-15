param(
    [string]$ConfigPath = "ops/guardian.config.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ConfigPath)) {
    throw "Config not found: $ConfigPath"
}

Write-Host "[guardian] starting with config: $ConfigPath"
python "ops/ai_guardian.py" --config $ConfigPath
