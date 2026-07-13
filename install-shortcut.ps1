param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $ProjectRoot 'launch.ps1'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Desktop launcher is missing: $launcher"
}

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'OKX BTC 策略观察台.lnk'
$powershell = Join-Path $PSHOME 'powershell.exe'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Port {1}' -f $launcher, $Port
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.Description = '打开本机 OKX BTC 永续合约策略观察台（只读公共行情）'

$edgeCandidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
$shortcut.IconLocation = if ($edge) { "$edge,0" } else { "$env:SystemRoot\System32\shell32.dll,220" }
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Host "Desktop shortcut installed: $shortcutPath" -ForegroundColor Green
