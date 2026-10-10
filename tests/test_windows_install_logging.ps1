$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $root 'packaging\windows\install-with-logs.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ('neurobridge-install-test-' + [guid]::NewGuid().ToString('N'))
$previousLocalData = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $env:LOCALAPPDATA = $work
    $hostExe = Join-Path $PSHOME 'powershell.exe'
    $common = '-NoProfile -ExecutionPolicy Bypass -File "' + $script + '"'
    $failed = Start-Process $hostExe -ArgumentList ($common + ' -Installer "' + (Join-Path $work 'missing.msi') + '"') -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'stdout.txt') -RedirectStandardError (Join-Path $work 'stderr.txt')
    if ($failed.ExitCode -eq 0) { throw 'Missing installer incorrectly reported success.' }
    $logs = @(Get-ChildItem (Join-Path $work 'NeuroBridge\installer-logs') -Filter 'install-*.log')
    if ($logs.Count -ne 1) { throw 'Failure did not persist an installation log.' }
    $text = Get-Content $logs[0].FullName -Raw
    if ($text -notmatch 'INSTALL_BEGIN' -or $text -notmatch 'INSTALL_END.*exit_code=1') { throw 'Failure/exit markers missing.' }
    if ($text -notmatch 'DIAGNOSTIC_CONTEXT.*packageApplicationVersion') { throw 'Failure log lacks package/environment context.' }
    $export = Start-Process $hostExe -ArgumentList ($common + ' -Export -OutputDirectory "' + $work + '"') -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'export-stdout.txt') -RedirectStandardError (Join-Path $work 'export-stderr.txt')
    if ($export.ExitCode -ne 0) { throw 'Export without an installed gateway failed.' }
    $archives = @(Get-ChildItem $work -Filter 'neurobridge-install-*.zip')
    if ($archives.Count -ne 1) { throw 'Expected one diagnostic ZIP.' }
    Expand-Archive -LiteralPath $archives[0].FullName -DestinationPath (Join-Path $work 'exported')
    if (@(Get-ChildItem (Join-Path $work 'exported') -File).Count -ne 2) { throw 'Unexpected exported files.' }
    $contextFile = Get-ChildItem (Join-Path $work 'exported') -Filter 'diagnostic-context-*.json'
    $context = Get-Content $contextFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($context.diagnosticScope -ne 'installation' -or $context.packageApplicationVersion -eq 'unknown' -or
        $context.osBuild -eq 'unknown' -or -not $context.nativeArchitecture) { throw 'Installation context incomplete.' }

    # An upgrade failure must retain distinct package and installed identities.
    . (Join-Path $root 'windows\diagnostic-context.ps1')
    $installed = Join-Path $work 'installed'
    $package = Join-Path $work 'package'
    New-Item -ItemType Directory -Path (Join-Path $installed 'neurobridge'), $package | Out-Null
    '[application]', 'version = "1.0.0"' | Set-Content (Join-Path $installed 'neurobridge\version_registry.toml')
    ('source_commit=' + ('a' * 40)) | Set-Content (Join-Path $installed 'build-info.txt')
    'application_version=2.0.0', ('source_commit=' + ('b' * 40)) | Set-Content (Join-Path $package 'build-info.txt')
    $context = Get-NeuroBridgeDiagnosticContext -Scope installation -ApplicationRoot $installed -PackageRoot $package
    if ($context.applicationVersion -ne '1.0.0' -or $context.packageApplicationVersion -ne '2.0.0' -or
        $context.sourceCommit -ne ('a' * 40) -or $context.packageSourceCommit -ne ('b' * 40)) {
        throw 'Installed and package versions were conflated.'
    }
    $unknown = Get-NeuroBridgeIdentity -Root (Join-Path $work 'absent')
    if ($unknown.ApplicationVersion -ne 'unknown' -or $unknown.SourceCommit -ne 'unknown') { throw 'Missing identity was guessed.' }

    # Exercise the runtime exporter without requiring a running service/Python.
    . (Join-Path $root 'windows\export-logs.ps1')
    function Get-ServiceSnapshot { param([string]$ServiceName); return "$ServiceName`: not installed" }
    function Get-WinEvent { throw 'Event query intentionally unavailable in test.' }
    $runtimeLogs = Join-Path $installed 'logs'
    New-Item -ItemType Directory -Path $runtimeLogs | Out-Null
    'runtime log entry' | Set-Content (Join-Path $runtimeLogs 'neurobridge.log')
    $config = Join-Path $installed 'gateway.toml'
    'secret-config-content' | Set-Content $config
    $layout = [pscustomobject]@{ Kind = 'package'; Root = $installed; DataRoot = $installed;
        Config = $config; Python = (Join-Path $installed 'missing-python.exe');
        ServiceName = 'NeuroBridge'; DefaultLogDirectory = $runtimeLogs }
    $runtimeArchive = Export-GatewayLogs -Layout $layout -OutputDirectory $work -NoSystem
    $runtimeExported = Join-Path $work 'runtime-exported'
    Expand-Archive -LiteralPath $runtimeArchive -DestinationPath $runtimeExported
    $runtimeContext = Get-Content (Join-Path $runtimeExported 'diagnostic-context.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runtimeContext.diagnosticScope -ne 'runtime' -or $runtimeContext.applicationVersion -ne '1.0.0' -or
        $runtimeContext.sourceCommit -ne ('a' * 40) -or $runtimeContext.pythonVersion -ne 'unknown' -or
        $runtimeContext.osBuild -eq 'unknown' -or $runtimeContext.osBits -ne 64) { throw 'Runtime context incomplete.' }
    foreach ($file in Get-ChildItem $runtimeExported -Recurse -File) {
        if ((Get-Content $file.FullName -Raw) -match 'secret-config-content') { throw 'Configuration contents leaked.' }
    }
    Write-Host 'Windows installation/runtime versions, environment and failure export verified.'
} finally {
    $env:LOCALAPPDATA = $previousLocalData
    Remove-Item -LiteralPath $work -Recurse -Force
}
