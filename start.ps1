param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$SkipInstall,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$url = "http://127.0.0.1:$Port"
$expectedAppId = 'okx-btc-advisor'
$expectedInstrument = 'BTC-USDT-SWAP'
$expectedVersion = '0.6.0'

function Test-LocalPortInUse {
    param([int]$TargetPort)
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $tcp.ConnectAsync('127.0.0.1', $TargetPort)
        return $task.Wait(500) -and $tcp.Connected
    }
    catch { return $false }
    finally { $tcp.Dispose() }
}

try {
    $health = Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 1
    if ($health.StatusCode -eq 200) {
        $identity = $health.Content | ConvertFrom-Json
        if ($identity.appId -eq $expectedAppId -and $identity.instrument -eq $expectedInstrument -and $identity.version -eq $expectedVersion) {
            Write-Host "OKX BTC Advisor is already running at $url" -ForegroundColor Green
            if (-not $NoBrowser) { Start-Process $url }
            exit 0
        }
        if ($identity.appId -eq $expectedAppId -and $identity.instrument -eq $expectedInstrument) {
            throw "Port $Port is running advisor version $($identity.version), but this release expects $expectedVersion. Stop the old service before restarting."
        }
    }
}
catch {
    # A refused connection is expected when the app has not started yet. Port ownership is
    # checked separately below so HTTP errors and non-JSON responses cannot be mistaken for it.
}

if (Test-LocalPortInUse $Port) {
    throw "Port $Port is already in use, but its /api/health endpoint did not identify '$expectedAppId'. Stop the other service or run: .\start.ps1 -Port 8766"
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
$rootVenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$devVenvPython = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $rootVenvPython) {
    $venvPython = $rootVenvPython
}
elseif (Test-Path -LiteralPath $devVenvPython) {
    $venvPython = $devVenvPython
}
else {
    if ($SkipInstall) {
        throw 'The Python virtual environment is missing. Run .\start.ps1 once without -SkipInstall.'
    }
    $python = Resolve-Tool 'python' @("$bundledRoot\python\python.exe")
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv.' }
    $venvPython = $rootVenvPython
}

if (-not $SkipInstall) {
    $pnpm = Resolve-Tool 'pnpm' @("$bundledRoot\bin\pnpm.cmd")
    $nodeDir = Split-Path (Resolve-Tool 'node' @("$bundledRoot\node\bin\node.exe"))
    $env:PATH = "$nodeDir;$bundledRoot\bin;$env:PATH"
    & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    Push-Location frontend
    try {
        & $pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        & $pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
    }
    finally {
        Pop-Location
    }
}

# -SkipInstall is intentionally strict: it must never start a half-installed server or silently
# serve an absent/stale development entrypoint.
& $venvPython -c "import fastapi, uvicorn, httpx, websockets, pydantic, numpy"
if ($LASTEXITCODE -ne 0) {
    throw 'Required Python packages are missing from the virtual environment. Run .\start.ps1 once without -SkipInstall.'
}
$distIndex = Join-Path $ProjectRoot 'frontend\dist\index.html'
$distAssets = Join-Path $ProjectRoot 'frontend\dist\assets'
if (-not (Test-Path -LiteralPath $distIndex -PathType Leaf) -or -not (Test-Path -LiteralPath $distAssets -PathType Container)) {
    throw 'The frontend production build is missing or incomplete. Run .\start.ps1 once without -SkipInstall.'
}
$distHtml = Get-Content -LiteralPath $distIndex -Raw -Encoding UTF8
$assetReferences = @(
    [regex]::Matches($distHtml, '(?:src|href)="(/assets/[^"?#]+)') |
        ForEach-Object { $_.Groups[1].Value } |
        Select-Object -Unique
)
$scriptReferences = @($assetReferences | Where-Object { $_ -match '\.js$' })
$missingAssets = @($assetReferences | Where-Object {
    $relativeAsset = $_.TrimStart('/').Replace('/', [IO.Path]::DirectorySeparatorChar)
    -not (Test-Path -LiteralPath (Join-Path (Split-Path $distIndex -Parent) $relativeAsset) -PathType Leaf)
})
if (-not $scriptReferences -or $missingAssets) {
    $missingSummary = if ($missingAssets) { $missingAssets -join ', ' } else { 'JavaScript entrypoint' }
    throw "The frontend production build references missing output: $missingSummary. Run .\start.ps1 once without -SkipInstall."
}
if ($SkipInstall) {
    $sourceFiles = @(
        Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'frontend\src') -File -Recurse -ErrorAction SilentlyContinue
        Get-Item (Join-Path $ProjectRoot 'frontend\index.html') -ErrorAction SilentlyContinue
        Get-Item (Join-Path $ProjectRoot 'frontend\package.json') -ErrorAction SilentlyContinue
        Get-Item (Join-Path $ProjectRoot 'frontend\pnpm-lock.yaml') -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'frontend') -Filter 'tsconfig*.json' -File -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'frontend') -Filter 'vite.config.*' -File -ErrorAction SilentlyContinue
    )
    $latestSource = $sourceFiles | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $builtAt = (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc
    if ($latestSource -and $latestSource.LastWriteTimeUtc -gt $builtAt.AddSeconds(1)) {
        throw "The frontend build is older than $($latestSource.Name). Run .\start.ps1 once without -SkipInstall."
    }
}

Write-Host "OKX BTC Advisor starting at $url" -ForegroundColor Cyan
Write-Host 'Press Ctrl+C to stop. Public market data only; no trading access.' -ForegroundColor Yellow

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($TargetUrl, $ExpectedAppId, $ExpectedInstrument, $ExpectedVersion)
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Milliseconds 500
            try {
                $health = Invoke-RestMethod "$TargetUrl/api/health" -UseBasicParsing -TimeoutSec 1
                if ($health.appId -eq $ExpectedAppId -and $health.instrument -eq $ExpectedInstrument -and $health.version -eq $ExpectedVersion) {
                    Start-Process $TargetUrl
                    break
                }
            }
            catch { }
        }
    } -ArgumentList $url, $expectedAppId, $expectedInstrument, $expectedVersion | Out-Null
}

& $venvPython -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
