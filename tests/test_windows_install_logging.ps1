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
    $export = Start-Process $hostExe -ArgumentList ($common + ' -Export -OutputDirectory "' + $work + '"') -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $work 'export-stdout.txt') -RedirectStandardError (Join-Path $work 'export-stderr.txt')
    if ($export.ExitCode -ne 0) { throw 'Export without an installed gateway failed.' }
    $archives = @(Get-ChildItem $work -Filter 'neurobridge-install-*.zip')
    if ($archives.Count -ne 1) { throw 'Expected one diagnostic ZIP.' }
    Expand-Archive -LiteralPath $archives[0].FullName -DestinationPath (Join-Path $work 'exported')
    if (@(Get-ChildItem (Join-Path $work 'exported') -File).Count -ne 1) { throw 'Unexpected exported files.' }
    Write-Host 'Windows installation failure and pre-install export verified.'
} finally {
    $env:LOCALAPPDATA = $previousLocalData
    Remove-Item -LiteralPath $work -Recurse -Force
}
