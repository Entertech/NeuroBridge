#Requires -Version 5.1
[CmdletBinding()]
param([switch]$Elevated)

$ErrorActionPreference = 'Stop'

function Invoke-NetworkCommand {
    param([string[]]$Arguments)
    Write-Host ('netsh ' + ($Arguments -join ' '))
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 wraps native stderr as ErrorRecord objects.
        # Inspect the exit code ourselves rather than treating all stderr as fatal.
        $ErrorActionPreference = 'Continue'
        $output = & "$env:SystemRoot\System32\netsh.exe" @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    $output | Out-Host
    if ($code -ne 0) { throw "netsh failed with exit code $code. No fallback will replace the existing network." }
}

function Test-DeploymentSubnetOverlap {
    param([string]$Prefix)
    $parts = $Prefix.Split('/')
    if ($parts.Count -ne 2) { return $false }
    $bits = [int]$parts[1]
    if ($bits -eq 0) { return $false }
    $address = [System.Net.IPAddress]::Parse($parts[0]).GetAddressBytes()
    if ($address.Length -ne 4) { return $false }
    $target = @(192, 168, 88, 0)
    for ($bit = 0; $bit -lt [Math]::Min($bits, 24); $bit++) {
        $index = [int][Math]::Floor($bit / 8)
        $mask = 128 -shr ($bit % 8)
        if (($address[$index] -band $mask) -ne ($target[$index] -band $mask)) { return $false }
    }
    return $true
}

function Set-BSideWiredAddress {
    param($Adapter)
    $fixedAddress = '192.168.88.20'
    $index = [int]$Adapter.ifIndex
    $allAddresses = @(Get-NetIPAddress -AddressFamily IPv4)
    $existing = @($allAddresses | Where-Object { $_.InterfaceIndex -eq $index })
    $interface = Get-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4
    $dhcpWasEnabled = [string]$interface.Dhcp -eq 'Enabled'
    Write-Host "Selected Ethernet: $($Adapter.Name), interface index $index, status $($Adapter.Status)"
    $existing | Select-Object IPAddress, PrefixLength, AddressState | Format-Table | Out-Host
    Write-Host "DHCP before configuration: $($interface.Dhcp)"
    if (@($allAddresses | Where-Object { $_.IPAddress -eq $fixedAddress -and $_.InterfaceIndex -ne $index }).Count -gt 0) {
        throw "$fixedAddress already belongs to another adapter. No address was changed."
    }
    foreach ($item in $existing) {
        if ($item.IPAddress -eq $fixedAddress) {
            if ($item.PrefixLength -ne 24) { throw 'The fixed address exists with another subnet mask. Resolve it manually.' }
            if ([string]$item.AddressState -in @('Duplicate', 'Invalid')) { throw 'Windows reports an invalid or duplicate fixed IP address.' }
            if ([string]$Adapter.Status -eq 'Disabled') { Enable-NetAdapter -Name $Adapter.Name -Confirm:$false }
            Write-Host "$fixedAddress/24 is already configured. Address state: $($item.AddressState)."
            return
        }
    }
    foreach ($route in @(Get-NetRoute -AddressFamily IPv4)) {
        if ((Test-DeploymentSubnetOverlap $route.DestinationPrefix) -and ($route.InterfaceIndex -ne $index -or $route.NextHop -ne '0.0.0.0')) {
            throw "Deployment subnet conflicts with route $($route.DestinationPrefix) on interface $($route.InterfaceIndex)."
        }
    }
    if ([string]$Adapter.Status -eq 'Disabled') {
        Enable-NetAdapter -Name $Adapter.Name -Confirm:$false
    }
    $added = $false
    try {
        if ($dhcpWasEnabled) {
            # Enabling coexistence must succeed before adding a static alias.
            # Do not silently disable DHCP on systems that lack this feature.
            Invoke-NetworkCommand -Arguments @('interface', 'ipv4', 'set', 'interface', "interface=$index", 'dhcpstaticipcoexistence=enabled', 'store=persistent')
        }
        Invoke-NetworkCommand -Arguments @('interface', 'ipv4', 'add', 'address', "name=$index", "address=$fixedAddress", 'mask=255.255.255.0', 'store=persistent')
        $added = $true
        $configured = $null
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            $configured = @(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 | Where-Object { $_.IPAddress -eq $fixedAddress }) | Select-Object -First 1
            if ($configured -and [string]$configured.AddressState -in @('Duplicate', 'Invalid')) { throw 'Invalid or duplicate IP detected; the added address will be removed.' }
            if ($configured -and [string]$configured.AddressState -eq 'Preferred') { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $configured -or $configured.PrefixLength -ne 24) { throw 'The fixed address could not be verified.' }
        $afterAddresses = @(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4)
        foreach ($item in $existing) {
            if ($item.IPAddress -notlike '169.254.*' -and -not ($afterAddresses.IPAddress -contains $item.IPAddress)) {
                throw "An existing address disappeared during configuration: $($item.IPAddress)."
            }
        }
        if ($dhcpWasEnabled -and [string](Get-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4).Dhcp -ne 'Enabled') {
            throw 'DHCP was unexpectedly disabled. Rolling back the added address.'
        }
        Write-Host "Configured $fixedAddress/24. No default gateway or DNS was added."
        Write-Host "Address state: $($configured.AddressState). A disconnected cable still needs a later connectivity check."
    }
    catch {
        $originalError = $_
        if ($added) {
            try {
                Invoke-NetworkCommand -Arguments @('interface', 'ipv4', 'delete', 'address', "name=$index", "address=$fixedAddress", 'store=persistent')
                if ($dhcpWasEnabled) { Set-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4 -Dhcp Enabled }
            }
            catch { Write-Warning "Rollback needs administrator attention: $($_.Exception.Message)" }
        }
        throw $originalError
    }
}

if ($env:OS -ne 'Windows_NT') { throw 'Run this tool on the Windows B-side computer, not on the gateway.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Administrator permission was not granted.' }
    $process = Start-Process -FilePath "$PSHOME\powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath), '-Elevated')
    exit $process.ExitCode
}

$result = 0
$transcribing = $false
try {
    $logDirectory = Join-Path $env:LOCALAPPDATA 'NeuroBridge\logs'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    $logPath = Join-Path $logDirectory ('b-side-ip-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Start-Transcript -Path $logPath -Force | Out-Null
    $transcribing = $true
    Write-Host 'NeuroBridge B-side Ethernet configuration: 192.168.88.20/24'
    Write-Host "Diagnostic log: $logPath"
    $adapters = @(Get-NetAdapter -Physical | Where-Object {
        [string]$_.PhysicalMediaType -eq '802.3' -or [int]$_.NdisPhysicalMedium -eq 14 -or
        ([int]$_.InterfaceType -eq 6 -and [int]$_.NdisPhysicalMedium -notin @(1, 9))
    } | Sort-Object ifIndex)
    if ($adapters.Count -eq 0) { throw 'No physical Ethernet adapter was identified. Check the USB adapter/driver; Wi-Fi and virtual adapters are not selected.' }
    $adapter = $adapters[0]
    if ($adapters.Count -gt 1) {
        $adapters | Select-Object ifIndex, Name, Status, InterfaceDescription | Format-Table | Out-Host
        $selection = 0
        if (-not [int]::TryParse((Read-Host 'Enter the interface index of the dedicated wired adapter'), [ref]$selection)) { throw 'Invalid interface index.' }
        $adapter = $adapters | Where-Object { $_.ifIndex -eq $selection } | Select-Object -First 1
        if (-not $adapter) { throw 'The selected index is not an eligible physical Ethernet adapter.' }
    }
    Set-BSideWiredAddress -Adapter $adapter
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 | Select-Object IPAddress, PrefixLength, AddressState | Format-Table | Out-Host
    Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 | Select-Object DestinationPrefix, NextHop, RouteMetric | Format-Table | Out-Host
    Write-Host 'IP configuration is complete. Open index.html to connect to ws://192.168.88.10:8765/neurobridge/v1/ws.'
}
catch {
    $result = 1
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
}
finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    Read-Host 'Press Enter to close' | Out-Null
}
exit $result
