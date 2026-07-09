# Run the FourFaced pipeline directly with Python (no Docker), using the
# official example clips in tests/sample_input/tasks.json.
# Usage: powershell -File scripts/run_local.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

New-Item -ItemType Directory -Force "$root\local_output" | Out-Null

$env:INPUT_PATH = "$root\tests\sample_input\tasks.json"
$env:OUTPUT_PATH = "$root\local_output\results.json"

python "$root\app\main.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python "$root\scripts\validate_output.py" $env:OUTPUT_PATH $env:INPUT_PATH
exit $LASTEXITCODE
