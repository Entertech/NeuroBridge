param(
    [Parameter(Mandatory = $true)][string]$PreviousInstaller,
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$SourceCommit,
    [Parameter(Mandatory = $true)][string]$LogDirectory
)

$ErrorActionPreference = 'Stop'
$installRoot = Join-Path $env:ProgramFiles 'NeuroBridge'
$dataRoot = Join-Path $env:ProgramData 'NeuroBridge'
$cliDirectory = Join-Path $installRoot 'windows\cli'

function Invoke-Installer {
    param([string]$File, [string]$Action, [string]$Log)
    $arguments = '/{0} /quiet /norestart /log "{1}"' -f $Action, $Log
    $process = Start-Process -FilePath (Resolve-Path -LiteralPath $File).Path -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin 0, 3010) {
        throw "NeuroBridge EXE $Action returned $($process.ExitCode). See $Log."
    }
}

if ((Test-Path -LiteralPath $installRoot) -or (Test-Path -LiteralPath $dataRoot) -or
    (Get-Service -Name NeuroBridge -ErrorAction SilentlyContinue)) {
    throw 'Run the EXE upgrade test on a clean, disposable Windows runner.'
}

Invoke-Installer $PreviousInstaller 'install' (Join-Path $LogDirectory 'previous-install.log')
$config = Join-Path $dataRoot 'gateway.toml'
Add-Content -LiteralPath $config -Value '# Preserve this configuration during the installer test' -Encoding UTF8
$configHash = (Get-FileHash -LiteralPath $config).Hash
Invoke-Installer $Installer 'install' (Join-Path $LogDirectory 'upgrade-install.log')
if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) {
    throw 'EXE upgrade changed the existing configuration.'
}
$buildInfo = Get-Content -LiteralPath (Join-Path $installRoot 'build-info.txt')
if ($buildInfo -notcontains "source_commit=$SourceCommit") {
    throw 'EXE upgrade did not install the expected source commit.'
}
$pathEntries = @([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';' |
    Where-Object { $_.TrimEnd('\') -eq $cliDirectory })
if ($pathEntries.Count -ne 1) {
    throw 'EXE upgrade must register the CLI directory exactly once in the machine PATH.'
}

$check = @'
$ErrorActionPreference = 'Stop'
$expected = Join-Path $env:ProgramFiles 'NeuroBridge\windows\cli\neurobridge.cmd'
if ((Get-Command neurobridge -CommandType Application).Source -ne $expected) {
    throw 'A fresh PowerShell did not resolve the installed command.'
}
neurobridge ssh status
exit $LASTEXITCODE
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($check))
$stdout = Join-Path $LogDirectory 'ssh-status.log'
$stderr = Join-Path $LogDirectory 'ssh-status-errors.log'
$shellOptions = @{
    FilePath = Join-Path $PSHOME 'powershell.exe'
    UseNewEnvironment = $true
    Wait = $true
    PassThru = $true
    ArgumentList = "-NoProfile -NonInteractive -EncodedCommand $encoded"
    RedirectStandardOutput = $stdout
    RedirectStandardError = $stderr
}
$shell = Start-Process @shellOptions
if ($shell.ExitCode -ne 0 -or -not (Select-String -LiteralPath $stdout -Pattern 'OpenSSH Server' -Quiet)) {
    throw "neurobridge ssh status failed. See $stdout and $stderr."
}

Invoke-Installer $Installer 'uninstall' (Join-Path $LogDirectory 'uninstall.log')
if (Test-Path -LiteralPath (Join-Path $cliDirectory 'neurobridge.cmd')) {
    throw 'The CLI remained installed after uninstall.'
}
if ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';' |
    Where-Object { $_.TrimEnd('\') -eq $cliDirectory }) {
    throw 'The CLI directory remained on the machine PATH after uninstall.'
}
if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) {
    throw 'Uninstall changed the existing configuration.'
}
