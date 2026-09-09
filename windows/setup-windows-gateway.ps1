#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('menu', 'prepare', 'start', 'check', 'config', 'algorithm', 'logs', 'diagnostics')]
    [string]$Action = 'prepare',
    [switch]$Offline
)

$ErrorActionPreference = 'Stop'
# Keep Chinese paths and messages intact when native Python output is redirected.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.runtime'
$projectPython = Join-Path $runtimeRoot 'windows-venv\Scripts\python.exe'
$helper = Join-Path $PSScriptRoot 'gateway_helper.py'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Executable" }
}

function Test-Python {
    param([string]$Executable, [string[]]$Prefix = @())
    try {
        & $Executable @Prefix -c "import sys, struct, platform; sys.exit(0 if sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64','x86_64') else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    $candidates = @(
        (Join-Path $projectRoot 'python-runtime\windows\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )
    foreach ($candidate in $candidates) {
        if ((Test-Path $candidate) -and (Test-Python $candidate)) { return $candidate }
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher -and (Test-Python $launcher.Source @('-3.11'))) {
        return (& $launcher.Source -3.11 -c 'import sys; print(sys.executable)')
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and (Test-Python $python.Source)) { return $python.Source }
    return $null
}

function Test-Dependencies {
    try {
        & $projectPython -c "import serial, websockets, bleak, win32service; from importlib.metadata import version; assert version('pyserial') == '3.5' and version('websockets') == '12.0' and version('bleak') == '0.19.0' and version('pywin32') == '306'" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Require-Runtime {
    if (-not (Test-Path $projectPython)) { throw 'Project Python is missing. Select 1 to prepare it.' }
    if (-not (Test-Python $projectPython)) { throw 'Project runtime must be Python 3.11 x64. Preserve the existing environment and repair it before retrying.' }
    if (-not (Test-Dependencies)) { throw 'Project dependencies are incomplete. Select 1 to repair them.' }
}

function Prepare-Runtime {
    if (-not (Test-Path $projectPython)) {
        $basePython = Find-Python
        if (-not $basePython) {
            if ($Offline) { throw 'Offline: supply Python 3.11 x64 with venv/pip under python-runtime\windows, or install it locally.' }
            $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
            if (-not $winget) { throw 'Install Python 3.11 x64 (with Launcher, venv and pip), then select 1 again. winget is unavailable.' }
            Write-Host 'Installing Python 3.11 x64 for the current user with winget...'
            Invoke-Checked $winget.Source @('install', '--id', 'Python.Python.3.11', '--exact', '--source', 'winget', '--architecture', 'x64', '--scope', 'user', '--accept-package-agreements', '--accept-source-agreements')
            $basePython = Find-Python
            if (-not $basePython) { throw 'Python installation completed but Python 3.11 x64 was not found. Reopen this launcher after checking installation.' }
        }
        Invoke-Checked $basePython @('-m', 'venv', (Join-Path $runtimeRoot 'windows-venv'))
    }
    if (-not (Test-Python $projectPython)) { throw 'Existing project Python is incompatible; it was not deleted or replaced.' }
    if (-not (Test-Dependencies)) {
        $installArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $projectRoot 'requirements.lock'))
        if ($Offline) {
            $wheelhouse = Join-Path $projectRoot 'wheelhouse\windows'
            if (-not (Test-Path $wheelhouse)) { throw 'Offline Windows wheels missing: wheelhouse\windows. No network install was attempted.' }
            $installArgs += @('--no-index', '--find-links', $wheelhouse)
        }
        Invoke-Checked $projectPython $installArgs
    }
    Invoke-Checked $projectPython @('-m', 'pip', 'check', '--disable-pip-version-check')
    Require-Runtime
}

function Invoke-Helper {
    param([string]$Operation)
    Require-Runtime
    Invoke-Checked $projectPython @($helper, $Operation)
}

function Invoke-Action {
    param([string]$Selected)
    if ($Selected -eq 'prepare') {
        Prepare-Runtime
        Invoke-Helper 'config'
        Invoke-Helper 'start'
    } else { Invoke-Helper $Selected }
}

try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) { throw 'This launcher requires Windows x64.' }
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'This source launcher requires Windows 10/11 and Python 3.11. Windows 7 runtime acceptance remains open.' }
    if (-not (Test-Path (Join-Path $projectRoot 'pyproject.toml')) -or -not (Test-Path $helper)) { throw 'Use the launcher from windows\ in a complete NeuroBridge checkout.' }
    Set-Location -LiteralPath $projectRoot
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    Write-Host "NeuroBridge Windows | project=$projectRoot | offline=$Offline"
    Write-Host 'Source is never fetched or updated. Foreground mode; no service is installed.'
    if ($Action -ne 'menu') {
        Invoke-Action $Action
    } else {
        while ($true) {
            Write-Host ''
            Write-Host '1. 一键准备并启动（推荐，前台运行）'
            Write-Host '2. 直接启动网关'
            Write-Host '4. 检查当前 USB / COM 串口'
            Write-Host '5. 创建或校验项目配置（保留已有设置）'
            Write-Host '6. 检查 Windows 算法程序'
            Write-Host '7. 导出诊断摘要（不含原始数据和日志正文）'
            Write-Host '8. 查看最近日志'
            Write-Host '0. 退出'
            $selection = Read-Host '请输入数字'
            if ($selection -eq '0') { break }
            $actions = @{ '1'='prepare'; '2'='start'; '4'='check'; '5'='config'; '6'='algorithm'; '7'='diagnostics'; '8'='logs' }
            if (-not $actions.ContainsKey($selection)) { Write-Host 'Select a listed number.'; continue }
            try { Invoke-Action $actions[$selection] }
            catch {
                Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
                Write-Host 'Fix the reported prerequisite, then retry. Configuration and recordings are preserved.'
            }
        }
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
exit 0
