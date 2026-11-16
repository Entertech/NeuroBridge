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

function Assert-VisibleInstallEntries {
    param([string]$Phase, [int]$ExpectedCount)
    $uninstallKeys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    $entries = @(foreach ($key in $uninstallKeys) {
        if (Test-Path -LiteralPath $key) {
            Get-ChildItem -LiteralPath $key | Get-ItemProperty | Where-Object {
                $_.DisplayName -in 'NeuroBridge', 'NeuroBridge Setup (in progress)' -and $_.SystemComponent -ne 1
            }
        }
    })
    $details = @($entries | Select-Object PSPath, DisplayName, DisplayVersion, WindowsInstaller, BundleVersion)
    $log = Join-Path $LogDirectory "$Phase-entries.json"
    ConvertTo-Json -InputObject $details | Set-Content -LiteralPath $log -Encoding UTF8
    if ($entries.Count -ne $ExpectedCount) {
        throw "Expected $ExpectedCount visible NeuroBridge entries at $Phase; found $($entries.Count). See $log."
    }
    if ($ExpectedCount -eq 1) {
        $versionLine = Get-Content -LiteralPath (Join-Path $installRoot 'build-info.txt') |
            Where-Object { $_ -like 'application_version=*' }
        if ($entries[0].DisplayName -ne 'NeuroBridge' -or $entries[0].WindowsInstaller -eq 1 -or
            $versionLine -ne "application_version=$($entries[0].BundleVersion)") {
            throw "Expected the installed NeuroBridge version to be listed as an EXE bundle at $Phase. See $log."
        }
    }
}

function Get-SshState {
    $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    $rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -PolicyStore ActiveStore -ErrorAction SilentlyContinue
    [ordered]@{
        State = $service.State
        StartMode = $service.StartMode
        Path = $service.PathName
        Firewall = $rule.Enabled -join ','
        RemoteAddress = if ($rule) { ($rule | Get-NetFirewallAddressFilter).RemoteAddress -join ',' }
    } | ConvertTo-Json -Compress
}

if ((Test-Path -LiteralPath $installRoot) -or (Test-Path -LiteralPath $dataRoot) -or
    (Get-Service -Name NeuroBridge -ErrorAction SilentlyContinue)) {
    throw 'Run the EXE upgrade test on a clean, disposable Windows runner.'
}
Assert-VisibleInstallEntries 'before-install' 0

$sshState = Get-SshState
Invoke-Installer $Installer 'install' (Join-Path $LogDirectory 'fresh-install.log')
Assert-VisibleInstallEntries 'fresh-install' 1
if ((Get-SshState) -ne $sshState) {
    throw 'Fresh EXE install changed the system SSH service or firewall rule.'
}
$cli = Join-Path $cliDirectory 'neurobridge.cmd'
if (-not (Get-Service -Name sshd -ErrorAction SilentlyContinue)) {
    $output = & $cli ssh enable
    if ($LASTEXITCODE -ne 1 -or "$output" -notmatch 'service \(sshd\) was not found') {
        throw "SSH enable did not report the missing service: $output"
    }
    if ((Get-SshState) -ne $sshState) {
        throw 'SSH enable changed system SSH state when the service was missing.'
    }
}
Invoke-Installer $Installer 'uninstall' (Join-Path $LogDirectory 'fresh-uninstall.log')
Assert-VisibleInstallEntries 'fresh-uninstall' 0
Remove-Item -LiteralPath $dataRoot -Recurse -Force

Invoke-Installer $PreviousInstaller 'install' (Join-Path $LogDirectory 'previous-install.log')
Assert-VisibleInstallEntries 'previous-install' 1
$config = Join-Path $dataRoot 'gateway.toml'
Add-Content -LiteralPath $config -Value '# Preserve this configuration during the installer test' -Encoding UTF8
$configHash = (Get-FileHash -LiteralPath $config).Hash
# Model an existing OpenSSH service which was previously enabled manually.
if (Get-Service -Name sshd -ErrorAction SilentlyContinue) {
    Stop-Service -Name sshd
    Set-Service -Name sshd -StartupType Manual
}
$sshState = Get-SshState
Invoke-Installer $Installer 'install' (Join-Path $LogDirectory 'upgrade-install.log')
Assert-VisibleInstallEntries 'upgrade-install' 1
if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) {
    throw 'EXE upgrade changed the existing configuration.'
}
if ((Get-SshState) -ne $sshState) {
    throw 'EXE upgrade changed the existing system SSH service or firewall rule.'
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
$status = neurobridge ssh status
$status
if (Get-Service -Name sshd -ErrorAction SilentlyContinue) {
    if ($LASTEXITCODE -ne 0 -or -not ($status -match '^SSHD executable: ')) {
        throw 'The installed CLI did not report the existing SSH server path.'
    }
} elseif ($LASTEXITCODE -ne 1 -or "$status" -notmatch 'service \(sshd\) was not found') {
    throw 'The installed CLI did not report that SSH is missing.'
}
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
    if ($shell.ExitCode -ne 0) {
        throw 'The installed CLI failed the web or SSH status check. See the status logs.'
    }
} finally {
    $shell.Dispose()
}

Invoke-Installer $Installer 'uninstall' (Join-Path $LogDirectory 'uninstall.log')
Assert-VisibleInstallEntries 'uninstall' 0
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

Invoke-Installer $Installer 'install' (Join-Path $LogDirectory 'reinstall.log')
Assert-VisibleInstallEntries 'reinstall' 1
if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) {
    throw 'Reinstall changed the existing configuration.'
}
if ((Get-SshState) -ne $sshState) {
    throw 'EXE reinstall changed the existing system SSH service or firewall rule.'
}
if (Get-Service -Name sshd -ErrorAction SilentlyContinue) {
    $status = & $cli ssh status
    $pathLines = @($status -match '^SSHD executable: ')
    if ($LASTEXITCODE -ne 0 -or $pathLines.Count -ne 1) {
        throw "Unable to determine the existing SSH server path: $status"
    }
    $serverPath = $pathLines[0] -replace '^SSHD executable: '
    $testPath = Join-Path $LogDirectory 'missing sshd.exe'
    $output = & $cli ssh enable $testPath
    if ($LASTEXITCODE -ne 1 -or "$output" -notmatch 'SSH executable was not found' -or (Get-SshState) -ne $sshState) {
        throw "SSH enable did not reject the missing executable path: $output"
    }
    $unregisteredPath = Join-Path $LogDirectory 'sshd.exe'
    New-Item -ItemType File -Path $unregisteredPath | Out-Null
    try {
        $output = & $cli ssh enable $unregisteredPath
        if ($LASTEXITCODE -ne 1 -or "$output" -notmatch 'does not match' -or (Get-SshState) -ne $sshState) {
            throw "SSH enable accepted an unregistered executable: $output"
        }
    } finally {
        Remove-Item -LiteralPath $unregisteredPath
    }
    $output = & $cli ssh enable $serverPath
    if ($LASTEXITCODE -ne 0 -or -not ($output -match '^OpenSSH Server: Running \(startup: Auto\)') -or
        -not ($output -match '^Windows SSH firewall rule: enabled=True, remote=LocalSubnet$')) {
        throw "SSH enable did not use the existing executable path and local-subnet firewall rule: $output"
    }
    & $cli ssh install
    if ($LASTEXITCODE -ne 0) {
        throw 'SSH install did not reuse the existing service.'
    }
}
Invoke-Installer $Installer 'uninstall' (Join-Path $LogDirectory 'reinstall-uninstall.log')
Assert-VisibleInstallEntries 'reinstall-uninstall' 0
