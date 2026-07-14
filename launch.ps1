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
$expectedVersion = '0.5.0'
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
        Show-LaunchFailure "检测到旧版 OKX BTC 策略观察台仍占用端口 $Port（运行版 $($existing.version)，当前版 $expectedVersion）。请先关闭旧服务，再双击桌面图标。"
        exit 1
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
