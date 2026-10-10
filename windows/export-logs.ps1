<#
.SYNOPSIS
Export NeuroBridge operational logs and service diagnostics for support.

.DESCRIPTION
Works for both deployment shapes on Windows x64:
  * source checkout -> <checkout>\.runtime\logs, service NeuroBridgeProject
  * MSI / EXE       -> %ProgramData%\NeuroBridge\logs, service NeuroBridge
The layout is detected, so the same command serves both.  This is the one tool
in windows\ that is correct for an installed package as well as a checkout.

The command is read-only: it never starts, stops or reconfigures the service,
and it never copies recordings, raw device data or the contents of the gateway
configuration.  Only the configuration SHA-256 is recorded.

.PARAMETER OutputDirectory
Directory for the archive.  Defaults to the current directory.

.PARAMETER MaxLogBytes
Copy at most the last N bytes of each log file.  Larger files are stored as
<name>.tail so the archive stays bounded.

.PARAMETER EventLogEntries
Maximum number of recent Application event-log entries to scan.

.PARAMETER NoSystem
Skip the COM-port and system inventory.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\windows\export-logs.ps1 -OutputDirectory C:\Temp

.EXAMPLE
# Installed from the MSI:
powershell -ExecutionPolicy Bypass -File 'C:\Program Files\NeuroBridge\windows\export-logs.ps1' -OutputDirectory C:\Temp
#>
[CmdletBinding()]
param(
    [string]$OutputDirectory = $PWD.ProviderPath,
    [int]$MaxLogBytes = 32MB,
    [int]$EventLogEntries = 400,
    [switch]$NoSystem
)

$ErrorActionPreference = 'Stop'
# Keep the export quiet when it is captured from a scheduled or support session.
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'diagnostic-context.ps1')

function Get-ProgramFilesDirectory {
    # A 32-bit host would otherwise resolve to "Program Files (x86)".
    if ($env:ProgramW6432) { return $env:ProgramW6432 }
    return $env:ProgramFiles
}

function Get-DeploymentLayout {
    param([string]$ScriptRoot)

    $checkout = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot '..')).ProviderPath
    $programData = Join-Path $env:ProgramData 'NeuroBridge'
    $packageConfig = Join-Path $programData 'gateway.toml'
    $installDirectory = Join-Path (Get-ProgramFilesDirectory) 'NeuroBridge'

    if (Test-Path -LiteralPath $packageConfig -PathType Leaf) {
        return [pscustomobject]@{
            Kind                = 'package'
            Root                = $installDirectory
            DataRoot            = $programData
            ServiceName         = 'NeuroBridge'
            Config              = $packageConfig
            Python              = (Join-Path $installDirectory 'runtime\python.exe')
            DefaultLogDirectory = (Join-Path $programData 'logs')
        }
    }

    $runtime = Join-Path $checkout '.runtime'
    return [pscustomobject]@{
        Kind                = 'source'
        Root                = $checkout
        DataRoot            = $runtime
        ServiceName         = 'NeuroBridgeProject'
        Config              = (Join-Path $runtime 'config\windows-gateway.toml')
        Python              = (Join-Path $runtime 'windows-venv\Scripts\python.exe')
        DefaultLogDirectory = (Join-Path $runtime 'logs')
    }
}

function Resolve-LogDirectory {
    param($Layout)

    if (Test-Path -LiteralPath $Layout.Python -PathType Leaf) {
        $snippet = 'import sys; sys.path.insert(0, sys.argv[1]); ' +
                   'from neurobridge.configuration.runtime import load_runtime_config; ' +
                   'print(load_runtime_config(sys.argv[2]).logging.directory)'
        try {
            $value = & $Layout.Python -c $snippet $Layout.Root $Layout.Config 2>$null
            if ($LASTEXITCODE -eq 0 -and $value) {
                $resolved = [string]($value | Select-Object -Last 1)
                $resolved = $resolved.Trim()
                if ($resolved) {
                    if (-not [System.IO.Path]::IsPathRooted($resolved)) {
                        $resolved = Join-Path $Layout.Root $resolved
                    }
                    return $resolved
                }
            }
        } catch {
            # Fall through to the layout default and record the reason.
            Write-Verbose ("Effective log path query failed: " + $_.Exception.Message)
        }
    }
    return $Layout.DefaultLogDirectory
}

function Copy-BoundedFile {
    param([string]$Source, [string]$Destination, [int]$MaxBytes)

    $info = Get-Item -LiteralPath $Source
    if ($info.Length -le $MaxBytes) {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
        return [pscustomobject]@{ Stored = $info.Length; Truncated = $false }
    }
    # Keep the most recent bytes: the tail explains a failure.
    $stream = [System.IO.File]::Open($Source, [System.IO.FileMode]::Open,
                                     [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $stream.Seek(-$MaxBytes, [System.IO.SeekOrigin]::End) | Out-Null
        $buffer = New-Object byte[] 65536
        $target = [System.IO.File]::Create($Destination)
        try {
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $target.Write($buffer, 0, $read)
            }
        } finally { $target.Dispose() }
    } finally { $stream.Dispose() }
    return [pscustomobject]@{ Stored = $MaxBytes; Truncated = $true }
}

function Get-ServiceSnapshot {
    param([string]$ServiceName)
    try {
        $service = Get-CimInstance Win32_Service -Filter ("Name='" + $ServiceName + "'") -ErrorAction Stop
        if ($null -eq $service) { return $ServiceName + ': not installed' }
        return ($service | Format-List Name,State,ProcessId,ExitCode,ServiceSpecificExitCode,StartMode,StartName,PathName |
                Out-String -Width 4096)
    } catch {
        return ('Service query failed: ' + $_.Exception.Message)
    }
}

function Export-GatewayLogs {
    param(
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [int]$MaxLogBytes = 32MB,
        [int]$EventLogEntries = 400,
        [switch]$NoSystem
    )

    if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
        throw "Output directory does not exist: $OutputDirectory"
    }
    $OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath

    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ('neurobridge-logs-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null

    $manifest = New-Object System.Collections.Generic.List[string]
    $inventory = New-Object System.Collections.Generic.List[string]
    $skipped = New-Object System.Collections.Generic.List[string]

    $logDirectory = Resolve-LogDirectory -Layout $Layout

    try {
        $context = Get-NeuroBridgeDiagnosticContext -Scope runtime -ApplicationRoot $Layout.Root -Python $Layout.Python
        $context | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $staging 'diagnostic-context.json') -Encoding UTF8
        $manifest.Add('NeuroBridge log export')
        $manifest.Add('generatedAtUtc: ' + [DateTime]::UtcNow.ToString('o'))
        $manifest.Add('layout: ' + $Layout.Kind)
        $manifest.Add('applicationRoot: ' + $Layout.Root)
        $manifest.Add('applicationVersion: ' + $context.applicationVersion)
        $manifest.Add('sourceCommit: ' + $context.sourceCommit)
        $manifest.Add('os: ' + $context.osName + ' version=' + $context.osVersion + ' build=' + $context.osBuild)
        $manifest.Add('architecture: ' + $context.nativeArchitecture + ' osBits=' + $context.osBits + ' exporterBits=' + $context.exporterBits)
        $manifest.Add('dataRoot: ' + $Layout.DataRoot)
        $manifest.Add('serviceName: ' + $Layout.ServiceName)
        $manifest.Add('logDirectory: ' + $logDirectory)
        $manifest.Add('elevated: ' + ([Security.Principal.WindowsPrincipal]::new(
            [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)))
        $manifest.Add('host: ' + [Environment]::MachineName + ' ' + [Environment]::OSVersion.VersionString)
        $manifest.Add('')
        $manifest.Add('Excluded by design:')
        $manifest.Add('  - gateway configuration contents (only its SHA-256 is recorded)')
        $manifest.Add('  - recordings and raw device data')
        $manifest.Add('  - credentials, tokens and private keys')
        $manifest.Add('')

        if (Test-Path -LiteralPath $Layout.Config -PathType Leaf) {
            $digest = (Get-FileHash -LiteralPath $Layout.Config -Algorithm SHA256).Hash.ToLowerInvariant()
            $inventory.Add('configSha256 ' + $Layout.Config + ': ' + $digest)
        } else {
            $skipped.Add('configuration not found: ' + $Layout.Config)
        }

        # ---- application logs -------------------------------------------------
        $logDestination = Join-Path $staging 'application-logs'
        New-Item -ItemType Directory -Path $logDestination -Force | Out-Null
        if (Test-Path -LiteralPath $logDirectory -PathType Container) {
            $candidates = @(Get-ChildItem -LiteralPath $logDirectory -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like '*.log*' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
                Sort-Object Name)
            if ($candidates.Count -eq 0) {
                $skipped.Add('no *.log* files under ' + $logDirectory)
            }
            foreach ($file in $candidates) {
                try {
                    $result = Copy-BoundedFile -Source $file.FullName `
                        -Destination (Join-Path $logDestination ($file.Name + $(if ($file.Length -gt $MaxLogBytes) { '.tail' } else { '' }))) `
                        -MaxBytes $MaxLogBytes
                    $note = 'log ' + $file.FullName + ' bytes=' + $file.Length
                    if ($result.Truncated) { $note += ' stored=tail(' + $MaxLogBytes + ')' }
                    $inventory.Add($note)
                } catch {
                    $skipped.Add('could not read log ' + $file.FullName + ': ' + $_.Exception.Message)
                }
            }
        } else {
            $skipped.Add('log directory not found: ' + $logDirectory)
        }

        # ---- service state ----------------------------------------------------
        $serviceDestination = Join-Path $staging 'service'
        New-Item -ItemType Directory -Path $serviceDestination -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $serviceDestination 'snapshot.txt') -Encoding UTF8 `
            -Value (Get-ServiceSnapshot -ServiceName $Layout.ServiceName)

        # ---- event log --------------------------------------------------------
        try {
            $events = @(Get-WinEvent -FilterHashtable @{
                LogName   = 'Application'
                StartTime = (Get-Date).AddDays(-7)
            } -MaxEvents $EventLogEntries -ErrorAction Stop |
                Where-Object { $_.ProviderName -like '*NeuroBridge*' -or $_.Message -like '*NeuroBridge*' })
            if ($events.Count -eq 0) {
                Set-Content -LiteralPath (Join-Path $serviceDestination 'event-log.txt') -Encoding UTF8 `
                    -Value 'No NeuroBridge entries in the Application log within the last 7 days.'
            } else {
                $events | ForEach-Object {
                    '[' + $_.TimeCreated.ToString('o') + '] ' + $_.LevelDisplayName + ' ' + $_.ProviderName + ': ' + $_.Message
                } | Set-Content -LiteralPath (Join-Path $serviceDestination 'event-log.txt') -Encoding UTF8
            }
        } catch {
            $skipped.Add('event log query failed: ' + $_.Exception.Message)
        }

        # ---- COM ports and system --------------------------------------------
        if (-not $NoSystem) {
            $systemDestination = Join-Path $staging 'system'
            New-Item -ItemType Directory -Path $systemDestination -Force | Out-Null
            $lines = New-Object System.Collections.Generic.List[string]
            $lines.Add('=== COM port devices ===')
            try {
                $ports = @(Get-PnpDevice -Class Ports -ErrorAction Stop |
                    Select-Object Status, FriendlyName, InstanceId)
                if ($ports.Count -eq 0) { $lines.Add('No devices reported for the Ports class.') }
                else { $ports | ForEach-Object { $lines.Add(($_.Status + ' ' + $_.FriendlyName + ' ' + $_.InstanceId)) } }
            } catch {
                $lines.Add('Get-PnpDevice unavailable: ' + $_.Exception.Message)
            }
            $lines.Add('')
            $lines.Add('=== Serial port inventory ===')
            try {
                $serial = @([System.IO.Ports.SerialPort]::GetPortNames())
                $lines.Add($(if ($serial.Count -eq 0) { 'No serial ports reported.' } else { $serial -join ', ' }))
            } catch {
                $lines.Add('Serial port enumeration failed: ' + $_.Exception.Message)
            }
            $lines | Set-Content -LiteralPath (Join-Path $systemDestination 'ports.txt') -Encoding UTF8
        } else {
            $skipped.Add('system inventory excluded by -NoSystem')
        }

        $manifest.Add('Skipped sections:')
        if ($skipped.Count -eq 0) { $manifest.Add('  (none)') }
        else { $skipped | ForEach-Object { $manifest.Add('  - ' + $_) } }
        $manifest.Add('')
        $manifest.Add('File inventory:')
        if ($inventory.Count -eq 0) { $manifest.Add('  (no log files or configuration digests were recorded)') }
        else { $inventory | ForEach-Object { $manifest.Add('  ' + $_) } }

        Set-Content -LiteralPath (Join-Path $staging 'manifest.txt') -Encoding UTF8 -Value $manifest

        $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
        $archive = Join-Path $OutputDirectory ('neurobridge-logs-' + $stamp + '.zip')
        Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $archive -Force
        $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        Set-Content -LiteralPath ($archive + '.sha256') -Encoding ASCII -Value ($hash + '  ' + (Split-Path -Leaf $archive))

        Write-Host ('Log export: ' + $archive) -ForegroundColor Cyan
        Write-Host ('Checksum:   ' + $archive + '.sha256')
        Write-Host ('Size:       ' + (Get-Item -LiteralPath $archive).Length + ' bytes')
        if ($skipped.Count -gt 0) {
            Write-Host 'Skipped:'
            $skipped | ForEach-Object { Write-Host ('  - ' + $_) }
        }
        Write-Host 'The archive excludes configuration contents, recordings and credentials.'
        return $archive
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Dot-sourcing loads the testable operations without producing an archive.
if ($MyInvocation.InvocationName -ne '.') {
    $result = 1
    try {
        $scriptRoot = $PSScriptRoot
        if (-not $scriptRoot) { $scriptRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path) }
        $layout = Get-DeploymentLayout -ScriptRoot $scriptRoot
        $archive = Export-GatewayLogs -Layout $layout -OutputDirectory $OutputDirectory `
            -MaxLogBytes $MaxLogBytes -EventLogEntries $EventLogEntries -NoSystem:$NoSystem
        if ($archive) { $result = 0 }
    } catch {
        Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    }
    exit $result
}
