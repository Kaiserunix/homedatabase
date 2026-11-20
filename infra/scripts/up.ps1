[CmdletBinding()]
param(
    [switch]$WithAi,
    [switch]$Tls,
    [switch]$Demo,
    [switch]$Bootstrap,
    [switch]$NoBuild,
    [switch]$Down,
    [switch]$Status,
    [string]$EnvFile = "infra/.env"
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $RootDir

try {
    $composeArgs = @("--env-file", $EnvFile, "-f", "infra/compose.yml")
    if ($Tls) {
        $composeArgs += @("-f", "infra/compose.tls.yml")
    }
    if ($WithAi) {
        $composeArgs += @("--profile", "ai-ocr")
    }

    if ($Down) {
        & docker compose @composeArgs down
        exit $LASTEXITCODE
    }
    if ($Status) {
        & docker compose @composeArgs ps
        exit $LASTEXITCODE
    }

    if (-not (Test-Path $EnvFile)) {
        if ($Tls) {
            Write-Error "Missing $EnvFile for -Tls mode. Run: python infra/scripts/init_env.py --mode production --host finance.example.com --output $EnvFile"
        }
        & python infra/scripts/init_env.py --mode local --output $EnvFile
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    if ($Tls) {
        & python infra/scripts/init_env.py --mode production --check $EnvFile
    } else {
        & python infra/scripts/init_env.py --check $EnvFile
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $upArgs = @("up", "-d", "--wait")
    if (-not $NoBuild) {
        $upArgs += "--build"
    }

    & docker compose @composeArgs @upArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if ($Demo) {
        & docker compose @composeArgs run --rm api homedatabase-seed --demo
    } elseif ($Bootstrap) {
        & docker compose @composeArgs run --rm api homedatabase-seed --bootstrap-family
    }

    $envLines = Get-Content $EnvFile
    $bindLine = ($envLines | Where-Object { $_ -match "^HOMEDATABASE_HTTP_BIND=" } | Select-Object -First 1)
    $portLine = ($envLines | Where-Object { $_ -match "^HOMEDATABASE_HTTP_PORT=" } | Select-Object -First 1)
    $httpBind = if ($bindLine) { ($bindLine -split "=", 2)[1].Trim() } else { "127.0.0.1" }
    $httpPort = if ($portLine) { ($portLine -split "=", 2)[1].Trim() } else { "8080" }
    if (-not $httpBind -or $httpBind -eq "0.0.0.0") { $httpBind = "localhost" }

    if ($WithAi -and ($envLines -contains "HOMEDATABASE_OCR_ENABLED=false")) {
        Write-Host ""
        Write-Host "AI OCR containers are running (--profile ai-ocr), with HOMEDATABASE_OCR_ENABLED=false."
        Write-Host "Authenticate Codex once before enabling:"
        Write-Host "  docker compose --env-file $EnvFile -f infra/compose.yml --profile ai-ocr run --rm ocr-runner codex login --device-auth"
        Write-Host "Then set HOMEDATABASE_OCR_ENABLED=true in $EnvFile and re-run this script."
    }

    Write-Host ""
    Write-Host "HomeDatabase Core Ledger is healthy:"
    Write-Host "  Mobile PWA:    http://${httpBind}:${httpPort}/"
    Write-Host "  Desktop Admin: http://${httpBind}:${httpPort}/admin.html"
    Write-Host "  API Health:    http://${httpBind}:${httpPort}/api/health"
    if (-not $Demo -and -not $Bootstrap) {
        Write-Host ""
        Write-Host "First-time setup (if no users exist yet):"
        Write-Host "  docker compose --env-file $EnvFile -f infra/compose.yml run --rm api homedatabase-seed --bootstrap-family"
    }
}
finally {
    Pop-Location
}
