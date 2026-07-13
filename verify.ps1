param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Resolve-Tool {
    param([string]$Name, [string[]]$Candidates)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        if (Test-Path -LiteralPath $expanded) { return $expanded }
    }
    throw "Cannot find $Name. Run this project from Codex Desktop or install the runtime."
}

$bundledRoot = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies"
$venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    Resolve-Tool 'python' @("$bundledRoot\python\python.exe")
}

Write-Host '1/5 Checking Windows launch scripts...' -ForegroundColor Cyan
foreach ($scriptName in @('start.ps1', 'launch.ps1', 'install-shortcut.ps1', 'verify.ps1')) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $ProjectRoot $scriptName),
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors) {
        $messages = ($parseErrors | ForEach-Object { $_.Message }) -join '; '
        throw ('PowerShell syntax validation failed for {0}: {1}' -f $scriptName, $messages)
    }
}

Write-Host '2/5 Compiling backend...' -ForegroundColor Cyan
& $python -m compileall -q backend
if ($LASTEXITCODE -ne 0) { throw 'Backend compilation failed.' }

Write-Host '3/5 Running backend tests...' -ForegroundColor Cyan
& $python -m pytest backend\tests
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }

if (-not $SkipFrontend) {
    $pnpm = Resolve-Tool 'pnpm' @("$bundledRoot\bin\pnpm.cmd")
    $nodeDir = Split-Path (Resolve-Tool 'node' @("$bundledRoot\node\bin\node.exe"))
    $env:PATH = "$nodeDir;$bundledRoot\bin;$env:PATH"
    Write-Host '4/5 Running frontend tests...' -ForegroundColor Cyan
    Push-Location frontend
    try {
        & $pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency verification failed.' }
        & $pnpm test
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
        Write-Host '5/5 Building production frontend...' -ForegroundColor Cyan
        & $pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
    }
    finally {
        Pop-Location
    }
} else {
    Write-Host '4/5 Frontend tests skipped by request.' -ForegroundColor DarkYellow
    Write-Host '5/5 Frontend build skipped by request.' -ForegroundColor DarkYellow
}

Write-Host 'Verification completed successfully.' -ForegroundColor Green
