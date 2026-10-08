#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('menu', 'prepare', 'start', 'check', 'config', 'algorithm', 'logs', 'diagnostics',
                 'autostart-enable', 'autostart-disable', 'autostart-status',
                 'export-logs', 'uninstall', 'purge', 'purge-internal')]
    [string]$Action = 'prepare',
    [switch]$Offline,
    [switch]$Yes,
    # Internal: target checkout for the staged 'purge-internal' removal step.
    # PowerShell variable names are case-insensitive, so this must not be named
    # like the $projectRoot script variable derived from $PSScriptRoot.
    [string]$RetireTarget = ''
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
$serviceName = 'NeuroBridgeProject'
$serviceAccount = 'LocalSystem'

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

function Prepare-Algorithm {
    Require-Runtime
    $buildArgs = @((Join-Path $PSScriptRoot 'algorithm_build.py'))
    if ($Offline) { $buildArgs += '--offline' }
    Invoke-Checked $projectPython $buildArgs
}

function Get-ServiceValue {
    param([string]$Operation)
    $value = & $projectPython (Join-Path $PSScriptRoot 'project_service.py') $Operation
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the project service; check for another installation.' }
    return $value
}

function Invoke-ServiceControl {
    param([string]$Operation)
    Require-Runtime
    $serviceScript = Join-Path $PSScriptRoot 'project_service.py'
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($Operation -eq 'status' -or $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Invoke-Checked $projectPython @($serviceScript, $Operation)
    } else {
        Write-Host 'Windows will request UAC permission to manage the boot service.'
        $process = Start-Process -FilePath $projectPython -ArgumentList @('-u', ('"' + $serviceScript + '"'), $Operation) -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw 'Service configuration failed. Check .runtime\logs\windows-service-control.log and windows-service.log. Autostart was not silently replaced with foreground mode.' }
    }
}

function Show-GatewayPage {
    $url = & $projectPython $helper 'url'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read the configured browser URL.' }
    Write-Host "Gateway is running in the background: $url"
    try { Start-Process -FilePath $url | Out-Null }
    catch { Write-Host "Open this URL in your browser: $url" }
}

function Start-ConfiguredGateway {
    $serviceState = Get-ServiceValue 'state'
    if ($serviceState -notin @('1', '4')) { throw 'Service is starting or stopping. Wait until it finishes, then retry.' }
    if ($serviceState -eq '4') {
        Write-Host 'Gateway service is already running; no duplicate process was started.'
        Show-GatewayPage
    } elseif ((Get-ServiceValue 'preference') -eq 'enabled') {
        Invoke-ServiceControl 'enable'
        Show-GatewayPage
    } else { Invoke-Helper 'start' }
}

function Invoke-Action {
    param([string]$Selected)
    if ($Selected -like 'autostart-*') {
        Invoke-ServiceControl ($Selected.Substring(10))
    } elseif ($Selected -eq 'start') {
        Require-Runtime
        Start-ConfiguredGateway
    } elseif ($Selected -eq 'prepare') {
        Prepare-Runtime
        if ((Get-ServiceValue 'state') -ne '1') {
            Start-ConfiguredGateway
            return
        }
        Invoke-Helper 'config'
        Prepare-Algorithm
        Start-ConfiguredGateway
    } elseif ($Selected -eq 'algorithm') {
        Require-Runtime
        if ((Get-ServiceValue 'state') -ne '1') { throw 'Stop the project service before rebuilding its algorithm.' }
        Prepare-Algorithm
    } else { Invoke-Helper $Selected }
}

# --- Retire: stop and delete this checkout's service, optionally its source ---

function Get-ProjectServiceObject {
    # Get-Service exposes a locale-independent status enum. The WMI object
    # carries the registered command line used for the ownership check.
    return (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)
}

function Assert-ProjectServiceOwnership {
    # Mirrors project_service.verify_owner: only this checkout's service may be
    # stopped or deleted. Unlike verify_owner this runs without the project
    # virtual environment, so a broken install can still be removed.
    # Ownership violations raise InvalidOperationException so the purge path can
    # tell "belongs to someone else, leave it alone" apart from "our own service
    # could not be removed, which must abort the purge".
    param([string]$Root)
    $cim = Get-CimInstance -ClassName Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
    if ($null -eq $cim) { return }
    $registered = ([string]$cim.PathName) -replace '\s+', ' '
    $expectedScript = Join-Path $Root 'windows\project_service.py'
    if ($registered.IndexOf($expectedScript, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw [System.InvalidOperationException]::new("$serviceName belongs to another installation; refusing to stop or delete it. Registered command: $registered")
    }
    if (-not $registered.TrimEnd().EndsWith('host', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw [System.InvalidOperationException]::new("$serviceName is not the project service host; refusing to stop or delete it.")
    }
    if ([string]$cim.StartName -ne $serviceAccount) {
        throw [System.InvalidOperationException]::new("$serviceName runs as '$($cim.StartName)'; refusing to change a service with another account.")
    }
}

function Get-ForegroundGatewayState {
    # A foreground gateway holds the project instance lock file open for its
    # whole run; a stale lock file left by a crash can still be opened.
    param([string]$Root)
    $lockPath = Join-Path $Root '.runtime\windows-gateway.lock'
    if (-not (Test-Path -LiteralPath $lockPath)) { return 'idle' }
    try {
        $stream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::Open,
                                         [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch [System.IO.IOException] {
        return 'running'
    }
    $stream.Close()
    return 'idle'
}

function Remove-ProjectService {
    param([string]$Root)
    Assert-ProjectServiceOwnership -Root $Root
    $service = Get-ProjectServiceObject
    if ($null -eq $service) {
        Write-Host "未发现 $serviceName；系统侧没有需要移除的服务注册。"
    } else {
        if ($service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Stopped) {
            Write-Host "正在停止 $serviceName ..."
            Stop-Service -Name $serviceName -Force -ErrorAction Stop
            $deadline = (Get-Date).AddSeconds(45)
            while ($true) {
                Start-Sleep -Milliseconds 500
                $current = Get-ProjectServiceObject
                if ($null -eq $current -or $current.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Stopped) { break }
                if ((Get-Date) -gt $deadline) { throw "$serviceName 未能在 45 秒内停止；请检查 .runtime\logs\windows-service.log。" }
            }
        }
        Write-Host "正在删除 $serviceName 服务注册 ..."
        $deleted = & sc.exe delete $serviceName 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "无法删除 $serviceName；请以管理员身份运行。输出：$deleted"
        }
        Write-Host "serviceRemoved=$serviceName"
    }
    $preference = Join-Path $Root '.runtime\config\windows-autostart.json'
    if (Test-Path -LiteralPath $preference) {
        Remove-Item -LiteralPath $preference -Force
        Write-Host 'autostartPreference=reset(default enabled)'
    }
}

function Backup-FieldData {
    param([string]$Root)
    $items = @()
    foreach ($relative in @('config', 'recordings')) {
        $path = Join-Path $Root ('.runtime\' + $relative)
        if (Test-Path -LiteralPath $path) { $items += $path }
    }
    if ($items.Count -eq 0) {
        Write-Host 'backup=skipped(no .runtime config or recordings)'
        return $null
    }
    $backupHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $target = Join-Path $backupHome "neurobridge-backup-$stamp.zip"
    Compress-Archive -LiteralPath $items -DestinationPath $target -CompressionLevel Optimal -Force
    Write-Host "backup=$target"
    Write-Host '提示：日志不在备份内；如需保留请另行复制 .runtime\logs'
    return $target
}

function Remove-ProjectTree {
    # Runs from the staged copy outside the checkout, because Windows keeps the
    # running launcher files locked and a tree cannot delete itself.
    param([string]$Target)
    if ([string]::IsNullOrWhiteSpace($Target)) { throw 'purge-internal requires -RetireTarget.' }
    $full = [System.IO.Path]::GetFullPath($Target)
    if ($full -eq [System.IO.Path]::GetPathRoot($full)) { throw "拒绝删除驱动器根目录：$full" }
    $protected = @($env:SystemRoot, $env:ProgramFiles, $env:ProgramData, $env:USERPROFILE) |
        Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\') }
    if ($protected -contains $full.TrimEnd('\')) { throw "拒绝删除系统或用户目录：$full" }
    if (-not (Test-Path -LiteralPath (Join-Path $full 'pyproject.toml'))) { throw "不是已校验的 NeuroBridge 项目：$full" }
    Write-Host "正在删除源码目录：$full"
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $full) {
        Write-Host 'sourceCheckout=partial'
        Write-Host '以下内容仍被当前运行中的启动器占用，未能删除：'
        Get-ChildItem -LiteralPath $full -Force -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host ("  " + $_.Name) }
        Write-Host '请关闭本窗口，然后在新的 PowerShell 中执行：'
        Write-Host ("  Remove-Item -LiteralPath '{0}' -Recurse -Force" -f $full)
        return 2
    }
    Write-Host "sourceCheckout=removed path=$full"
    Write-Host '提示：原项目目录已不存在，请关闭本窗口。'
    return 0
}

function Invoke-Purge {
    Write-Host ''
    Write-Host '即将移除服务注册、备份现场数据并删除整个源码目录：'
    Write-Host "  $projectRoot"
    Write-Host '删除后无法恢复；配置、算法、日志与录制都会一并消失。'
    if ($Yes) {
        Write-Host '确认继续？ [-Yes: 已自动确认]'
    } else {
        $answer = Read-Host '确认继续？请输入 DELETE'
        if ($answer -ne 'DELETE') { Write-Host '已取消，未做任何改动。'; return $false }
    }
    if ((Get-ForegroundGatewayState -Root $projectRoot) -eq 'running') {
        throw '前台网关仍在运行。请先在该窗口按 Ctrl+C 正常退出，再重新执行本入口。'
    }
    try {
        Remove-ProjectService -Root $projectRoot
    } catch [System.InvalidOperationException] {
        # A service registered by a different checkout must never be touched,
        # but it must not block removing this checkout either.
        Write-Host "WARNING: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host '已跳过服务移除（该服务不属于本项目）；本项目的源码目录仍会删除，请另行处理该服务。'
    }
    Backup-FieldData -Root $projectRoot | Out-Null
    # Stage a copy outside the checkout and delete from there.
    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ('neurobridge-retire-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    $staged = Join-Path $staging 'retire.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $staged -Force
    $hostExe = (Get-Process -Id $PID).Path
    # The staged child streams its progress to the console; keep it out of the
    # pipeline so Invoke-Purge still returns a single boolean.
    & $hostExe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $staged -Action 'purge-internal' -RetireTarget $projectRoot | Out-Host
    $code = $LASTEXITCODE
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    if ($code -ne 0) {
        throw '源码目录未删除；请按上面的输出处理后再重试。'
    }
    return $true
}

function Invoke-Retire {
    param([string]$Root)
    Write-Host ''
    Write-Host '一键退出（移除服务注册与源码）'
    Write-Host '  1. 只移除服务注册（保留源码与 .runtime 数据）'
    Write-Host '  2. 移除服务注册、备份现场数据并删除整个源码目录'
    Write-Host '  0. 返回主菜单'
    $choice = Read-Host '请输入选项 [0-2]'
    switch ($choice) {
        '1' {
            if ($Yes) {
                Write-Host '确认继续？ [-Yes: 已自动确认]'
            } else {
                $answer = Read-Host "将停止并移除 $serviceName 服务注册，源码目录与 .runtime 数据会保留。是否继续？[yes/no]"
                if ($answer -notin @('yes', 'y', 'YES', 'Y')) { Write-Host '已取消，未做任何改动。'; return $false }
            }
            Remove-ProjectService -Root $Root
            Write-Host "移除完成：服务注册已删除，源码目录保留在 $Root"
            return $false
        }
        '2' { return (Invoke-Purge) }
        '0' { return $false }
        default { Write-Host '无效选项，请输入 0 到 2。'; return $false }
    }
}

function Invoke-LogExport {
    param([string]$Root)
    $exportScript = Join-Path $Root 'windows\export-logs.ps1'
    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        throw "Missing log exporter: $exportScript"
    }
    # The exporter ends with 'exit', so run it in a child process: that keeps it
    # from terminating this launcher and keeps its output off our pipeline.
    $hostExe = (Get-Process -Id $PID).Path
    & $hostExe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $exportScript `
        -OutputDirectory (Get-Location).ProviderPath | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Log export failed with exit code $LASTEXITCODE" }
}

try {
    if ($Action -eq 'purge-internal') {
        exit (Remove-ProjectTree -Target $RetireTarget)
    }
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) { throw 'This launcher requires Windows x64.' }
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'This source launcher requires Windows 10/11 and Python 3.11. Windows 7 runtime acceptance remains open.' }
    if (-not (Test-Path (Join-Path $projectRoot 'pyproject.toml')) -or -not (Test-Path $helper)) { throw 'Use the launcher from windows\ in a complete NeuroBridge checkout.' }
    Set-Location -LiteralPath $projectRoot
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    Write-Host "NeuroBridge Windows | project=$projectRoot | offline=$Offline"
    Write-Host 'Source is never fetched or updated. Boot autostart is enabled by default; saved opt-out uses foreground mode.'
    if ($Action -ne 'menu') {
        if ($Action -eq 'uninstall') {
            Remove-ProjectService -Root $projectRoot
            Write-Host "移除完成：服务注册已删除，源码目录保留在 $projectRoot"
        } elseif ($Action -eq 'purge') {
            Invoke-Purge | Out-Null
        } elseif ($Action -eq 'export-logs') {
            Invoke-LogExport -Root $projectRoot
        } else {
            Invoke-Action $Action
        }
    } else {
        while ($true) {
            Write-Host ''
            Write-Host '1. 一键准备并启动（默认开机自启）'
            Write-Host '2. 直接启动网关'
            Write-Host '4. 检查当前 USB / COM 串口'
            Write-Host '5. 创建或校验项目配置（保留已有设置）'
            Write-Host '6. 准备 / 修复 Windows 算法程序'
            Write-Host '7. 导出诊断摘要（不含原始数据和日志正文）'
            Write-Host '8. 查看最近日志'
            Write-Host '9. 查看开机自启状态'
            Write-Host '10. 启用开机自启并启动'
            Write-Host '11. 关闭开机自启并停止服务'
            Write-Host '12. 一键退出（移除服务注册与源码）'
            Write-Host '13. 导出运行日志（源码/安装包通用）'
            Write-Host '0. 退出'
            $selection = Read-Host '请输入数字'
            if ($selection -eq '0') { break }
            if ($selection -eq '13') {
                try { Invoke-LogExport -Root $projectRoot }
                catch {
                    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
                    Write-Host 'No configuration or recording file was modified.'
                }
                continue
            }
            if ($selection -eq '12') {
                try { if (Invoke-Retire -Root $projectRoot) { break } }
                catch {
                    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
                    Write-Host 'Fix the reported prerequisite, then retry. Configuration and recordings are preserved.'
                }
                continue
            }
            $actions = @{ '1'='prepare'; '2'='start'; '4'='check'; '5'='config'; '6'='algorithm'; '7'='diagnostics'; '8'='logs'; '9'='autostart-status'; '10'='autostart-enable'; '11'='autostart-disable' }
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
