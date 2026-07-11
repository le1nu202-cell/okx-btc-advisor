param(
    [int]$Port = 8765,
    [switch]$SkipInstall,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$url = "http://127.0.0.1:$Port"

try {
    $health = Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 1
    if ($health.StatusCode -eq 200) {
        Write-Host "OKX BTC Advisor is already running at $url" -ForegroundColor Green
        if (-not $NoBrowser) { Start-Process $url }
        exit 0
    }
}
catch {
    # No existing local service; continue with normal startup.
}

function Resolve-Tool {
    param([string]$Name, [string[]]$Candidates)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        if (Test-Path -LiteralPath $expanded) { return $expanded }
    }
    throw "Cannot find $Name. Install the runtime or start this project from Codex Desktop."
}

$bundledRoot = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies"
$python = Resolve-Tool 'python' @("$bundledRoot\python\python.exe")
$pnpm = Resolve-Tool 'pnpm' @("$bundledRoot\bin\pnpm.cmd")
$nodeDir = Split-Path (Resolve-Tool 'node' @("$bundledRoot\node\bin\node.exe"))
$env:PATH = "$nodeDir;$bundledRoot\bin;$env:PATH"

$rootVenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$devVenvPython = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $rootVenvPython) {
    $venvPython = $rootVenvPython
}
elseif (Test-Path -LiteralPath $devVenvPython) {
    $venvPython = $devVenvPython
}
else {
    & $python -m venv .venv
    $venvPython = $rootVenvPython
    if ($SkipInstall) {
        throw 'Dependencies are not installed yet. Run start.ps1 once without -SkipInstall.'
    }
}

if (-not $SkipInstall) {
    & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
    Push-Location frontend
    try {
        & $pnpm install --frozen-lockfile
        & $pnpm build
    }
    finally {
        Pop-Location
    }
}

Write-Host "OKX BTC Advisor starting at $url" -ForegroundColor Cyan
Write-Host 'Press Ctrl+C to stop. Public market data only; no trading access.' -ForegroundColor Yellow

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($TargetUrl)
        Start-Sleep -Seconds 2
        Start-Process $TargetUrl
    } -ArgumentList $url | Out-Null
}

& $venvPython -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
