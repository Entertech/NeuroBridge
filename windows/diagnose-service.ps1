param([switch]$Elevated)

$ErrorActionPreference = 'Stop'

function Get-GatewayServiceSnapshot {
    Get-CimInstance Win32_Service -Filter "Name='NeuroBridgeProject'"
}

function Start-ProjectServiceIfStopped {
    param([string]$ProjectRoot)
    $snapshot = Get-GatewayServiceSnapshot
    if ($null -eq $snapshot) {
        throw 'NeuroBridgeProject is not installed. Use neurobridge-windows-bootstrap.cmd to prepare it.'
    }
    $expectedHost = Join-Path $ProjectRoot 'windows\project_service.py'
    $pattern = '^"[^"]+"\s+-I\s+-S\s+-u\s+"' + [regex]::Escape($expectedHost) + '"\s+host\s*$'
    if ($snapshot.PathName -notmatch $pattern -or $snapshot.StartName -ne 'LocalSystem') {
        throw 'The service command or account does not match this checkout. No service action was taken.'
    }
    if ($snapshot.State -eq 'Running') {
        return 'Service is already running; no restart was performed.'
    }
    if ($snapshot.State -ne 'Stopped') {
        throw ('Service is ' + $snapshot.State + '. Wait for it to finish, then run this tool again.')
    }
    Start-Service -Name NeuroBridgeProject -ErrorAction Stop
    $controller = Get-Service -Name NeuroBridgeProject
    $controller.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(45))
    return 'Service reached Running. Check the capture page and runtime logs to verify data acquisition.'
}

function Invoke-ServiceDiagnosis {
    param([string]$ProjectRoot)
    $directory = Join-Path $ProjectRoot '.runtime\diagnostics'
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss.fffffffZ')
    $report = Join-Path $directory ('windows-service-diagnosis-' + $stamp + '-' + [Guid]::NewGuid().ToString('N') + '.txt')
    $exitCode = 0
    function Write-Report {
        param([string]$Text)
        Write-Host $Text
        Add-Content -LiteralPath $report -Value $Text -Encoding UTF8
    }
    function Write-Snapshot {
        param([string]$Stage)
        Write-Report ("`r`n=== " + $Stage + ' (UTC ' + [DateTime]::UtcNow.ToString('o') + ') ===')
        try {
            $service = Get-GatewayServiceSnapshot
            if ($null -eq $service) { Write-Report 'NeuroBridgeProject: not installed' }
            else {
                Write-Report ($service | Format-List Name,State,ProcessId,ExitCode,ServiceSpecificExitCode,StartMode,StartName,PathName | Out-String -Width 4096)
            }
        } catch { Write-Report ('Service query failed: ' + $_.Exception.Message) }
        try {
            $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' })
            if ($processes.Count -eq 0) { Write-Report 'No python.exe/pythonw.exe processes found.' }
            else {
                Write-Report ($processes | Format-List ProcessId,ParentProcessId,ExecutablePath,CommandLine | Out-String -Width 4096)
            }
        } catch { Write-Report ('Process query failed: ' + $_.Exception.Message) }
    }
    Write-Report ('Project: ' + $ProjectRoot)
    Write-Report 'This tool does not stop processes, delete locks, change service configuration, or upload files.'
    Write-Snapshot 'Before start'
    try { Write-Report (Start-ProjectServiceIfStopped -ProjectRoot $ProjectRoot) }
    catch {
        $exitCode = 1
        Write-Report ('START FAILED: ' + $_.Exception.Message)
        Write-Report 'If a foreground gateway window is open, press Ctrl+C in that window, wait for cleanup, then double-click this tool again.'
        Write-Report 'Otherwise provide this report for diagnosis. Do not delete the lock or terminate unrelated Python processes.'
    }
    Write-Snapshot 'After start attempt'
    $hostLog = Join-Path $ProjectRoot '.runtime\logs\windows-service.log'
    if (Test-Path -LiteralPath $hostLog -PathType Leaf) {
        try {
            Write-Report "`r`n=== Recent service host log (last 200 lines) ==="
            Write-Report ((Get-Content -LiteralPath $hostLog -Tail 200 -Encoding UTF8) -join "`r`n")
        } catch { Write-Report ('Could not read service host log: ' + $_.Exception.Message) }
    }
    Write-Host "`r`nReport saved: $report" -ForegroundColor Cyan
    Write-Host 'Review process command lines for secrets before sharing this report. No configuration or recording files were copied.'
    return $exitCode
}

# Dot-sourcing loads the testable operations without elevation or side effects.
if ($MyInvocation.InvocationName -ne '.') {
    $result = 1
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            if ($Elevated) { throw 'Administrator permission was not granted.' }
            Write-Host 'Requesting administrator permission to inspect and start the project service...'
            $child = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Elevated') -Verb RunAs -Wait -PassThru
            $result = $child.ExitCode
        } else {
            $root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
            $result = Invoke-ServiceDiagnosis -ProjectRoot $root
        }
    } catch {
        Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    } finally {
        if ($Elevated) { Read-Host 'Press Enter to close this administrator window' | Out-Null }
    }
    exit $result
}
