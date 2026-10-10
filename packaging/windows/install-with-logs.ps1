# Invoke from the extracted delivery directory; logging works before installation.
[CmdletBinding()]
param(
    [string]$Installer,
    [switch]$Export,
    [string]$OutputDirectory = (Get-Location).Path
)
$ErrorActionPreference = 'Stop'
$logDirectory = Join-Path $env:LOCALAPPDATA 'NeuroBridge\installer-logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N')
if ($Export) {
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $archive = Join-Path $OutputDirectory ('neurobridge-install-' + $stamp + '.zip')
    $files = @(Get-ChildItem -LiteralPath $logDirectory -File | Where-Object {
        $_.Name -like 'install-*.log*' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
    })
    if ($files.Count -eq 0) { throw 'No installation logs. Run installation through this script first.' }
    Compress-Archive -LiteralPath $files.FullName -DestinationPath $archive
    Write-Host ('Log export: ' + $archive)
    exit 0
}
$summary = Join-Path $logDirectory ('install-' + $stamp + '.log')
$nativeLog = $summary + '.native.log'
$result = 1
try {
    ('INSTALL_BEGIN utc=' + [DateTime]::UtcNow.ToString('o')) | Set-Content -LiteralPath $summary -Encoding UTF8
    $os = Get-CimInstance Win32_OperatingSystem
    ('OS=' + $os.Caption + ' version=' + $os.Version + ' architecture=' + $os.OSArchitecture) | Add-Content -LiteralPath $summary -Encoding UTF8
    if (-not [Environment]::Is64BitOperatingSystem -or [version]$os.Version -lt [version]'10.0') {
        throw 'Only Windows 10/11 x64 is supported.'
    }
    if ([string]::IsNullOrWhiteSpace($Installer)) { throw 'Specify -Installer <path to EXE or MSI>.' }
    $path = (Resolve-Path -LiteralPath $Installer).Path
    if ($path.Contains('"')) { throw 'Installer path contains a quote.' }
    ('installer=' + [IO.Path]::GetFileName($path) + ' sha256=' + (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash) | Add-Content -LiteralPath $summary -Encoding UTF8
    switch ([IO.Path]::GetExtension($path).ToLowerInvariant()) {
        '.msi' { $executable = 'msiexec.exe'; $arguments = '/i "' + $path + '" /L*V "' + $nativeLog + '"' }
        '.exe' { $executable = $path; $arguments = '/log "' + $nativeLog + '"' }
        default { throw 'Installer must be an EXE or MSI.' }
    }
    $process = Start-Process -FilePath $executable -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    $result = $process.ExitCode
    if ($result -notin 0, 1641, 3010) { throw ('Installation failed: exit_code=' + $result) }
    Write-Host ('Installer completed: exit_code=' + $result + '. Verify service, ports and capture separately.')
} catch {
    $_.Exception.Message | Add-Content -LiteralPath $summary -Encoding UTF8
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
} finally {
    ('INSTALL_END utc=' + [DateTime]::UtcNow.ToString('o') + ' exit_code=' + $result) | Add-Content -LiteralPath $summary -Encoding UTF8
    Write-Host ('Installation logs: ' + $logDirectory)
    Write-Host 'Export: powershell -NoProfile -ExecutionPolicy Bypass -File .\install-with-logs.ps1 -Export'
    Get-ChildItem -LiteralPath $logDirectory -File | Where-Object { $_.Name -like 'install-*.log*' } |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -Skip 20 | Remove-Item -Force
}
exit $result
