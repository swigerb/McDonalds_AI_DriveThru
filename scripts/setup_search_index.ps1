#!/usr/bin/env pwsh
# setup_search_index.ps1 — Wrapper that invokes setup_search_index.py via the repo venv.
# azd runs hooks from the project root, so resolve paths from this script's location.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = Split-Path -Parent $ScriptDir

# load_python_env.ps1 provisions the venv at repo root .venv
& "$ScriptDir\load_python_env.ps1"

$venvPythonPath = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path -Path "/usr") {
    # Linux/macOS fallback
    $venvPythonPath = Join-Path $RepoRoot ".venv/bin/python"
}

$pythonScriptPath = Join-Path $RepoRoot "app\backend\setup_search_index.py"

Write-Host "Running setup_search_index.py..."
& $venvPythonPath $pythonScriptPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "setup_search_index.py failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
