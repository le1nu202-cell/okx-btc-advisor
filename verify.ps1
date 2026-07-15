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

$testDatabaseRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("okx-advisor-verify-{0}" -f [Guid]::NewGuid().ToString('N'))
$testDatabasePath = Join-Path $testDatabaseRoot 'pytest.db'
$protectedEnvironment = @('OKX_ADVISOR_DB', 'OKX_DISABLE_NETWORK', 'OKX_PYTEST_DB')
$originalEnvironment = @{}
foreach ($name in $protectedEnvironment) {
    $originalEnvironment[$name] = @{
        Present = Test-Path -LiteralPath "Env:$name"
        Value = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
}
New-Item -ItemType Directory -Path $testDatabaseRoot -ErrorAction Stop | Out-Null

try {
    # Never inherit a user's real database path into pytest. conftest.py also
    # validates this explicit, non-existent path before backend.main is loaded.
    $env:OKX_PYTEST_DB = $testDatabasePath
    $env:OKX_ADVISOR_DB = $testDatabasePath
    $env:OKX_DISABLE_NETWORK = '1'

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

    Write-Host '3/5 Running backend tests with an isolated temporary database...' -ForegroundColor Cyan
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
}
finally {
    try {
        foreach ($artifact in @($testDatabasePath, "$testDatabasePath-wal", "$testDatabasePath-shm", "$testDatabasePath-journal")) {
            if (Test-Path -LiteralPath $artifact) {
                Remove-Item -LiteralPath $artifact -Force -ErrorAction SilentlyContinue
            }
        }
        if ((Test-Path -LiteralPath $testDatabaseRoot) -and -not (Get-ChildItem -LiteralPath $testDatabaseRoot -Force)) {
            Remove-Item -LiteralPath $testDatabaseRoot -Force -ErrorAction SilentlyContinue
        }
    }
    finally {
        foreach ($name in $protectedEnvironment) {
            $saved = $originalEnvironment[$name]
            if ($saved.Present) {
                [Environment]::SetEnvironmentVariable($name, [string]$saved.Value, 'Process')
            } else {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
        }
    }
}
