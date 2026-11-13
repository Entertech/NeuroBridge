param(
    [ValidateSet('Menu', 'Enable', 'Disable', 'Status')]
    [string]$Action = 'Menu',
    [switch]$PauseOnExit,
    [switch]$RequireGateway,
    [switch]$FromInstaller
)

$ErrorActionPreference = 'Stop'
$capability = 'OpenSSH.Server~~~~0.0.1.0'
$ruleName = 'OpenSSH-Server-In-TCP'

function Show-SshStatus {
    $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    if ($service) {
        Write-Host "OpenSSH Server: $($service.State) (startup: $($service.StartMode))"
    } else {
        Write-Host 'OpenSSH Server is not installed.'
    }

    $rule = Get-NetFirewallRule -Name $ruleName -PolicyStore ActiveStore -ErrorAction SilentlyContinue
    if ($rule) {
        $scope = ($rule | Get-NetFirewallAddressFilter).RemoteAddress -join ', '
        Write-Host "Windows SSH firewall rule: enabled=$($rule.Enabled), remote=$scope"
    } else {
        Write-Host 'Windows SSH firewall rule is missing.'
    }

    if ($service -and $service.State -eq 'Running') {
        $listeners = Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -eq $service.ProcessId }
        if ($listeners) {
            Write-Host 'From another computer on the local network, use your Windows account:'
            Get-NetIPConfiguration | ForEach-Object { $_.IPv4Address } | Where-Object {
                $_.IPAddress -and $_.IPAddress -notlike '169.254.*' -and
                    ($listeners.LocalAddress -contains '0.0.0.0' -or $listeners.LocalAddress -contains $_.IPAddress)
            } | ForEach-Object {
                Write-Host "  ssh WindowsUser@$($_.IPAddress)"
            }
        } else {
            Write-Host 'sshd is running but is not listening on TCP port 22. Check sshd_config.'
        }
    }
}

$exitCode = 0
try {
    if ($RequireGateway -and -not (Get-Service -Name NeuroBridge -ErrorAction SilentlyContinue)) {
        throw 'Install the NeuroBridge Windows gateway before using this command.'
    }

    if ($Action -eq 'Menu') {
        Write-Host 'NeuroBridge Windows remote support'
        Write-Host "Signed-in user running this tool: $([Environment]::UserName)"
        Write-Host '1. Enable SSH for the local subnet and start it at boot'
        Write-Host '2. Stop Windows SSH and disable its firewall rule'
        Write-Host '3. Check SSH status'
        Write-Host '0. Exit'
        switch (Read-Host 'Choose an action') {
            '1' { $Action = 'Enable' }
            '2' { $Action = 'Disable' }
            '3' { $Action = 'Status' }
            '0' { exit 0 }
            default { throw 'Choose 1, 2, 3 or 0.' }
        }
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if ($FromInstaller) {
            throw 'The installer must configure OpenSSH with administrator privileges.'
        }
        $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -Action {1} -PauseOnExit' -f $PSCommandPath, $Action
        if ($RequireGateway) {
            $arguments += ' -RequireGateway'
        }
        $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        exit $process.ExitCode
    }

    if ($Action -eq 'Enable') {
        $restartNeeded = $false
        if ((Get-WindowsCapability -Online -Name $capability).State -ne 'Installed') {
            Write-Host 'Installing the Windows OpenSSH Server feature...'
            $restartNeeded = (Add-WindowsCapability -Online -Name $capability).RestartNeeded
        }

        if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule -Name $ruleName -Enabled True -RemoteAddress LocalSubnet
        } else {
            New-NetFirewallRule -Name $ruleName -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress LocalSubnet | Out-Null
        }

        $service = Get-Service -Name sshd -ErrorAction SilentlyContinue
        if (-not $service) {
            if ($restartNeeded) {
                throw 'Windows needs a restart before the SSH service can be configured. Restart and run neurobridge ssh enable.'
            }
            throw 'OpenSSH Server installation did not register the sshd service.'
        }
        Set-Service -Name sshd -StartupType Automatic
        if ($restartNeeded) {
            Write-Warning 'Windows needs a restart to finish installing OpenSSH Server. SSH is configured to start at boot.'
        } else {
            Start-Service -Name sshd
        }
    } elseif ($Action -eq 'Disable') {
        if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
            Disable-NetFirewallRule -Name $ruleName
        }
        if (Get-Service -Name sshd -ErrorAction SilentlyContinue) {
            Set-Service -Name sshd -StartupType Manual
            Stop-Service -Name sshd
        }
    }

    if (-not $FromInstaller) {
        Show-SshStatus
    }
} catch {
    $failure = "Remote support setup failed: " + $_.Exception.Message
    Write-Host $failure -ForegroundColor Red
    if ($FromInstaller) {
        & "$env:SystemRoot\System32\eventcreate.exe" /T WARNING /ID 100 /L APPLICATION /SO 'NeuroBridge SSH Setup' /D $failure | Out-Null
    }
    $exitCode = 1
} finally {
    if ($PauseOnExit) {
        [void](Read-Host 'Press Enter to close this window')
    }
}
exit $exitCode
