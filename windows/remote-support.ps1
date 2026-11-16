param(
    [ValidateSet('Menu', 'Install', 'Enable', 'Disable', 'Status')]
    [string]$Action = 'Menu',
    [string]$SshdPath,
    [switch]$PauseOnExit,
    [switch]$RequireGateway
)

$ErrorActionPreference = 'Stop'
$capability = 'OpenSSH.Server~~~~0.0.1.0'
$ruleName = 'OpenSSH-Server-In-TCP'

function Get-RegisteredSshdPath($Service) {
    if ($Service.PathName -notmatch '^\s*(?:"(?<path>[^"]+)"|(?<path>[^"]+?\.exe))(?:\s|$)') {
        throw 'Cannot determine the executable path of the registered sshd service.'
    }
    $path = [Environment]::ExpandEnvironmentVariables($Matches['path'])
    $executable = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
    if ($executable -isnot [IO.FileInfo] -or $executable.Name -ne 'sshd.exe') {
        throw "Cannot find sshd.exe at the registered service path: $path"
    }
    return $executable.FullName
}

function Show-SshStatus($ServerPath) {
    $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    Write-Host "OpenSSH Server: $($service.State) (startup: $($service.StartMode))"
    Write-Host "SSHD executable: $ServerPath"

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
        Write-Host '4. Install and enable Windows OpenSSH Server'
        Write-Host '0. Exit'
        switch (Read-Host 'Choose an action') {
            '1' { $Action = 'Enable' }
            '2' { $Action = 'Disable' }
            '3' { $Action = 'Status' }
            '4' { $Action = 'Install' }
            '0' { exit 0 }
            default { throw 'Choose 1, 2, 3, 4 or 0.' }
        }
    }

    if ($SshdPath) {
        if ($Action -ne 'Enable') {
            throw 'Specify an sshd.exe path only with neurobridge ssh enable.'
        }
        $executable = Get-Item -LiteralPath $SshdPath -ErrorAction SilentlyContinue
        if ($executable -isnot [IO.FileInfo]) {
            throw "SSH executable was not found: $SshdPath"
        }
        $SshdPath = $executable.FullName
    }

    $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    if (-not $service -and $Action -ne 'Install') {
        throw 'OpenSSH Server service (sshd) was not found. Run neurobridge ssh install to install it.'
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -Action {1} -PauseOnExit' -f $PSCommandPath, $Action
        if ($SshdPath) {
            $arguments += ' -SshdPath "{0}"' -f $SshdPath
        }
        if ($RequireGateway) {
            $arguments += ' -RequireGateway'
        }
        $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        exit $process.ExitCode
    }

    if ($Action -eq 'Install' -and -not $service) {
        if ((Get-WindowsCapability -Online -Name $capability).State -ne 'Installed') {
            Write-Host '正在通过 Windows 安装 OpenSSH Server，可能需要下载组件，请等待安装完成。'
            if ((Add-WindowsCapability -Online -Name $capability).RestartNeeded) {
                Write-Warning 'Windows needs a restart to finish installing OpenSSH Server. After restarting, run neurobridge ssh enable.'
                exit 0
            }
            $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
        }
        if (-not $service) {
            throw 'Windows OpenSSH Server is installed, but the sshd service is missing. Check the Windows OpenSSH installation.'
        }
    }
    $serverPath = Get-RegisteredSshdPath $service
    if ($SshdPath -and $SshdPath -ne $serverPath) {
        throw "The registered sshd service uses '$serverPath'. The specified path '$SshdPath' does not match."
    }

    if ($Action -in 'Enable', 'Install') {
        if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule -Name $ruleName -Enabled True -RemoteAddress LocalSubnet
        } else {
            New-NetFirewallRule -Name $ruleName -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress LocalSubnet | Out-Null
        }

        Set-Service -Name sshd -StartupType Automatic
        Start-Service -Name sshd
    } elseif ($Action -eq 'Disable') {
        if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
            Disable-NetFirewallRule -Name $ruleName
        }
        Set-Service -Name sshd -StartupType Manual
        Stop-Service -Name sshd
    }

    Show-SshStatus $serverPath
} catch {
    $failure = "Remote support setup failed: " + $_.Exception.Message
    Write-Host $failure -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($PauseOnExit) {
        [void](Read-Host 'Press Enter to close this window')
    }
}
exit $exitCode
