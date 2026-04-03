# Production mode: pass -Production flag to skip frontend rebuild and use gunicorn
param(
    [switch]$Production,
    [switch]$GPU
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptRoot "..")

Push-Location $repoRoot
try {
    & "$repoRoot/scripts/load_python_env.ps1"

    # Fix onnxruntime for GPU (DirectML) — faster-whisper pulls in CPU variant which conflicts
    if ($GPU) {
        Write-Host ""
        Write-Host "GPU mode: checking onnxruntime variant..."
        $pipPath = Join-Path $repoRoot ".venv/scripts/pip.exe"
        if ($IsLinux -or $IsMacOS) { $pipPath = Join-Path $repoRoot ".venv/bin/pip" }
        
        # Check if onnxruntime-directml is already the active variant
        $dmlInstalled = & $pipPath show onnxruntime-directml 2>$null
        if ($dmlInstalled) {
            Write-Host "GPU mode: onnxruntime-directml already installed — skipping swap"
        } else {
            Write-Host "GPU mode: swapping onnxruntime CPU → DirectML..."
            & $pipPath uninstall onnxruntime -y 2>$null
            & $pipPath install --force-reinstall --no-deps onnxruntime-directml==1.24.4 --quiet
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARNING: Could not install onnxruntime-directml (offline?). GPU may not be available."
            } else {
                Write-Host "GPU mode: onnxruntime-directml ready"
            }
        }
        Write-Host ""
    }

    if (-not $Production) {
        Write-Host ""
        Write-Host "Restoring frontend npm packages"
        Write-Host ""
        Push-Location "$repoRoot/app/frontend"
        try {
            # Use --prefer-offline so npm doesn't hang when network is down
            npm install --prefer-offline
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARNING: npm install failed (offline?). Using existing node_modules."
            }

            Write-Host ""
            Write-Host "Building frontend"
            Write-Host ""
            npm run build
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to build frontend"
            }
        }
        finally {
            Pop-Location
        }
    }

    Write-Host ""
    Write-Host "Starting backend"
    Write-Host ""
    Push-Location "$repoRoot/app/backend"
    try {
        $venvPythonPath = Join-Path $repoRoot ".venv/scripts/python.exe"
        if ($IsLinux -or $IsMacOS) {
            $venvPythonPath = Join-Path $repoRoot ".venv/bin/python"
        }
        if ($Production) {
            # Production: gunicorn with aiohttp worker
            if (-not $env:HOST) { $env:HOST = "0.0.0.0" }
            if (-not $env:PORT) { $env:PORT = "8000" }
            if (-not $env:LOG_LEVEL) { $env:LOG_LEVEL = "info" }
            $env:RUNNING_IN_PRODUCTION = "true"
            Start-Process -FilePath $venvPythonPath -ArgumentList @(
                "-m", "gunicorn", "app:create_app",
                "-b", "$($env:HOST):$($env:PORT)",
                "--worker-class", "aiohttp.GunicornWebWorker",
                "--workers", "2",
                "--timeout", "120",
                "--keep-alive", "65",
                "--access-logfile", "-",
                "--log-level", $env:LOG_LEVEL
            ) -Wait -NoNewWindow
        } else {
            # Development: direct aiohttp
            Start-Process -FilePath $venvPythonPath -ArgumentList "-m app" -Wait -NoNewWindow
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start backend"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
