param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$SkipInstall,
    [switch]$NoBrowser,
    [switch]$NoPopup
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:$Port"
$healthUrl = "$url/api/health"
$expectedAppId = 'okx-btc-advisor'
$expectedInstrument = 'BTC-USDT-SWAP'
$expectedVersion = '0.6.0'
$logDir = Join-Path $ProjectRoot 'logs'
$launchId = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
$stdoutLog = Join-Path $logDir "desktop-launch-$launchId.stdout.log"
$stderrLog = Join-Path $logDir "desktop-launch-$launchId.stderr.log"

function Open-Advisor {
    if (-not $NoBrowser) { Start-Process $url }
}

function Show-LaunchFailure {
    param([string]$Message)
    if ($NoPopup) {
        [Console]::Error.WriteLine($Message)
        return
    }
    (New-Object -ComObject WScript.Shell).Popup($Message,0,'OKX BTC 策略观察台',16) | Out-Null
}

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

function Stop-VerifiedStaleAdvisor {
    param([int]$TargetPort)

    try {
        $ownerProcessIds = @(
            Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction Stop |
                Where-Object { $_.LocalAddress -eq '127.0.0.1' } |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
        if ($ownerProcessIds.Count -ne 1) { return $false }

        $ownerProcessId = [int]$ownerProcessIds[0]
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerProcessId" -ErrorAction Stop
        if (-not $process -or $process.Name -notmatch '^python(?:w)?\.exe$') { return $false }

        # A matching health response alone is not enough authority to terminate a process.
        # Restrict automatic upgrades to Python runtimes this project is allowed to launch,
        # plus the exact loopback uvicorn command used by start.ps1.
        $allowedExecutables = @(
            (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
            (Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'),
            (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
        ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            ForEach-Object { [IO.Path]::GetFullPath($_) }
        $executablePath = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
        $approvedExecutable = $allowedExecutables | Where-Object {
            [string]::Equals($_, $executablePath, [StringComparison]::OrdinalIgnoreCase)
        }
        if (-not $approvedExecutable) { return $false }

        $commandLine = [string]$process.CommandLine
        $portPattern = '(?i)(?:^|\s)--port\s+{0}(?:\s|$)' -f [regex]::Escape([string]$TargetPort)
        if ($commandLine -notmatch '(?i)(?:^|\s)-m\s+uvicorn\s+backend\.main:app(?:\s|$)' -or
            $commandLine -notmatch '(?i)(?:^|\s)--host\s+127\.0\.0\.1(?:\s|$)' -or
            $commandLine -notmatch $portPattern) {
            return $false
        }

        Stop-Process -Id $ownerProcessId -ErrorAction Stop
        for ($attempt = 0; $attempt -lt 100; $attempt++) {
            if (-not (Test-LocalPortInUse -TargetPort $TargetPort)) { return $true }
            Start-Sleep -Milliseconds 100
        }
        return $false
    }
    catch {
        return $false
    }
}

# Reusing an already-running, correctly identified instance avoids trying to redirect a
# second server into log files that may still be open. It also makes repeated double-clicks
# effectively idempotent.
try {
    $existing = Invoke-RestMethod $healthUrl -UseBasicParsing -TimeoutSec 1
    if ($existing.appId -eq $expectedAppId -and $existing.instrument -eq $expectedInstrument -and $existing.version -eq $expectedVersion) {
        Open-Advisor
        exit 0
    }
    if ($existing.appId -eq $expectedAppId -and $existing.instrument -eq $expectedInstrument) {
        if (-not (Stop-VerifiedStaleAdvisor -TargetPort $Port)) {
            Show-LaunchFailure "检测到旧版 OKX BTC 策略观察台仍占用端口 $Port（运行版 $($existing.version)，当前版 $expectedVersion）。启动器无法安全确认旧进程，因此没有自动关闭它。请先关闭旧服务，再双击桌面图标。"
            exit 1
        }
    }
}
catch {
    # A refused connection is the normal cold-start path.
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$startScript = Join-Path $ProjectRoot 'start.ps1'
$arguments = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $startScript),
    '-Port', ([string]$Port),
    '-NoBrowser'
)
if ($SkipInstall) { $arguments += '-SkipInstall' }

try {
    $server = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

    # A normal launch may need to create the local environment and production build. Five
    # minutes leaves room for a first install while still reporting a bounded failure.
    for ($attempt = 0; $attempt -lt 600; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod $healthUrl -UseBasicParsing -TimeoutSec 1
            if ($health.appId -eq $expectedAppId -and $health.instrument -eq $expectedInstrument -and $health.version -eq $expectedVersion) {
                Open-Advisor
                exit 0
            }
        }
        catch { }
        if ($server.HasExited) { break }
    }

    $details = @()
    if (Test-Path -LiteralPath $stderrLog) {
        $details += Get-Content -LiteralPath $stderrLog -Encoding UTF8 -Tail 20 -ErrorAction SilentlyContinue
    }
    if (-not $details -and (Test-Path -LiteralPath $stdoutLog)) {
        $details += Get-Content -LiteralPath $stdoutLog -Encoding UTF8 -Tail 20 -ErrorAction SilentlyContinue
    }
    $summary = if ($details) { ($details -join "`n") } else { '服务未在 5 分钟内通过健康检查。' }
    $message = "OKX BTC 策略观察台启动失败。`n`n$summary`n`n日志：$stderrLog"
    Show-LaunchFailure $message
    exit 1
}
catch {
    $message = "OKX BTC 策略观察台启动失败。`n`n$($_.Exception.Message)`n`n项目：$ProjectRoot"
    Show-LaunchFailure $message
    exit 1
}
