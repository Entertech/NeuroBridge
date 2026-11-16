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
# Model an existing OpenSSH service which was previously enabled manually.
if (Get-Service -Name sshd -ErrorAction SilentlyContinue) {
    Stop-Service -Name sshd
    Set-Service -Name sshd -StartupType Manual
}
Invoke-Installer $Installer 'install' (Join-Path $LogDirectory 'upgrade-install.log')
if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) {
    throw 'EXE upgrade changed the existing configuration.'
}
$sshService = Get-CimInstance Win32_Service -Filter "Name='sshd'"
$sshListener = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq $sshService.ProcessId }
if (-not $sshListener) {
    throw 'OpenSSH did not listen on TCP port 22 after the EXE upgrade.'
}
$buildInfo = Get-Content -LiteralPath (Join-Path $installRoot 'build-info.txt')
if ($buildInfo -notcontains "source_commit=$SourceCommit") {
    throw 'EXE upgrade did not install the expected source commit.'
}
$python = Join-Path $installRoot 'runtime\python.exe'
& $python -c "import bleak; from importlib.metadata import version; assert version('bleak') == '0.19.5'; print('Installed Bleak', version('bleak'))"
if ($LASTEXITCODE -ne 0) {
    throw 'EXE upgrade did not install Bleak 0.19.5.'
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
$url = neurobridge web --url
if ($LASTEXITCODE -ne 0 -or $url -notmatch '^http://127\.0\.0\.1:[0-9]+/$') {
    throw 'The installed web command did not return a local gateway URL.'
}
$ProgressPreference = 'SilentlyContinue'
$page = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
if ($page.StatusCode -ne 200) {
    throw 'The installed web command returned an unavailable URL.'
}
Write-Output "NeuroBridge web URL verified: $url"
neurobridge ssh status
exit $LASTEXITCODE
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($check))
$shellInfo = New-Object System.Diagnostics.ProcessStartInfo
$shellInfo.FileName = Join-Path $PSHOME 'powershell.exe'
$shellInfo.Arguments = "-NoProfile -NonInteractive -EncodedCommand $encoded"
$shellInfo.UseShellExecute = $false
$shellInfo.RedirectStandardOutput = $true
$shellInfo.RedirectStandardError = $true
# Keep the runner's normal process environment. Reload the PATH exactly from
# its persistent scopes so the test sees what a newly opened terminal sees.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$shellInfo.EnvironmentVariables['Path'] = (@($machinePath, $userPath) | Where-Object { $_ }) -join ';'
$shell = [Diagnostics.Process]::Start($shellInfo)
try {
    $stdout = $shell.StandardOutput.ReadToEndAsync()
    $stderr = $shell.StandardError.ReadToEndAsync()
    if (-not $shell.WaitForExit(30000)) {
        $shell.Kill()
        throw 'The SSH status command timed out.'
    }
    $output = $stdout.GetAwaiter().GetResult()
    Set-Content -LiteralPath (Join-Path $LogDirectory 'ssh-status.log') -Value $output -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $LogDirectory 'ssh-status-errors.log') -Value ($stderr.GetAwaiter().GetResult()) -Encoding UTF8
    if ($shell.ExitCode -ne 0 -or $output -notmatch 'OpenSSH Server: Running \(startup: Auto\)' -or
        $output -notmatch '(?m)^Windows SSH firewall rule: enabled=True, remote=LocalSubnet\s*$') {
        throw 'The installer did not enable automatic, local-subnet SSH. See the status logs.'
    }
} finally {
    $shell.Dispose()
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
