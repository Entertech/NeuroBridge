#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../web/b-client-test/configure-wired-ip.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ($parseErrors | Out-String) }
# Load only function definitions. Never execute UAC, transcripts or native network commands.
foreach ($definition in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    Invoke-Expression $definition.Extent.Text
}

function Assert-True { param($Value, [string]$Message) if (-not $Value) { throw $Message } }
function Reset-Network {
    $script:Addresses = @([pscustomobject]@{ InterfaceIndex = 5; IPAddress = '10.20.30.40'; PrefixLength = 24; AddressState = 'Preferred' })
    $script:Routes = @([pscustomobject]@{ InterfaceIndex = 5; DestinationPrefix = '0.0.0.0/0'; NextHop = '10.20.30.1' })
    $script:Dhcp = 'Enabled'
    $script:Commands = @()
    $script:FailCoexistence = $false
    $script:BreakDhcpAfterAdd = $false
}
function Get-NetIPAddress {
    param($InterfaceIndex, $AddressFamily)
    if ($PSBoundParameters.ContainsKey('InterfaceIndex')) {
        return @($script:Addresses | Where-Object { $_.InterfaceIndex -eq $InterfaceIndex })
    }
    return $script:Addresses
}
function Get-NetIPInterface { param($InterfaceIndex, $AddressFamily) return [pscustomobject]@{ Dhcp = $script:Dhcp } }
function Get-NetRoute { param($AddressFamily) return $script:Routes }
function Start-Sleep { param($Milliseconds) }
function Set-NetIPInterface { param($InterfaceIndex, $AddressFamily, $Dhcp) $script:Dhcp = $Dhcp }
function Enable-NetAdapter { param($Name, $Confirm) }
function Invoke-NetworkCommand {
    param([string[]]$Arguments)
    $script:Commands += ($Arguments -join ' ')
    if ($Arguments -contains 'dhcpstaticipcoexistence=enabled' -and $script:FailCoexistence) { throw 'unsupported coexistence' }
    if ($Arguments[2] -eq 'add') {
        $script:Addresses += [pscustomobject]@{ InterfaceIndex = 5; IPAddress = '192.168.88.20'; PrefixLength = 24; AddressState = 'Preferred' }
        if ($script:BreakDhcpAfterAdd) { $script:Dhcp = 'Disabled' }
    }
    if ($Arguments[2] -eq 'delete') { $script:Addresses = @($script:Addresses | Where-Object { $_.IPAddress -ne '192.168.88.20' }) }
}

$adapter = [pscustomobject]@{ ifIndex = 5; Name = 'Wired port with spaces'; Status = 'Up' }
Reset-Network
Set-BSideWiredAddress -Adapter $adapter
Assert-True ($script:Addresses.Count -eq 2) 'The original LAN address was not preserved.'
Assert-True ($script:Dhcp -eq 'Enabled') 'DHCP was not preserved.'
Assert-True ($script:Commands[0] -match 'dhcpstaticipcoexistence=enabled') 'Coexistence must be enabled before adding an address.'
Assert-True ($script:Commands[1] -match 'store=persistent') 'The address should survive a reboot.'
Assert-True ($script:Routes[0].NextHop -eq '10.20.30.1') 'The default route changed.'
Set-BSideWiredAddress -Adapter $adapter
Assert-True ($script:Commands.Count -eq 2) 'Repeated execution added a duplicate address.'

Reset-Network
$script:FailCoexistence = $true
$failed = $false
try { Set-BSideWiredAddress -Adapter $adapter } catch { $failed = $true }
Assert-True $failed 'Unsupported coexistence must fail.'
Assert-True ($script:Commands.Count -eq 1 -and $script:Addresses.Count -eq 1) 'A failed coexistence check must not change addresses.'

Reset-Network
$script:BreakDhcpAfterAdd = $true
$failed = $false
try { Set-BSideWiredAddress -Adapter $adapter } catch { $failed = $true }
Assert-True $failed 'Unexpected DHCP loss must not be reported as success.'
Assert-True ($script:Addresses.Count -eq 1 -and $script:Dhcp -eq 'Enabled') 'Rollback did not restore the previous network.'

Reset-Network
$script:Addresses += [pscustomobject]@{ InterfaceIndex = 8; IPAddress = '192.168.88.20'; PrefixLength = 24; AddressState = 'Preferred' }
$failed = $false
try { Set-BSideWiredAddress -Adapter $adapter } catch { $failed = $true }
Assert-True ($failed -and $script:Commands.Count -eq 0) 'An address conflict on another adapter must not modify the network.'

Reset-Network
$script:Routes += [pscustomobject]@{ InterfaceIndex = 8; DestinationPrefix = '192.168.0.0/16'; NextHop = '0.0.0.0' }
$failed = $false
try { Set-BSideWiredAddress -Adapter $adapter } catch { $failed = $true }
Assert-True ($failed -and $script:Commands.Count -eq 0) 'Overlapping routes on other adapters must not be replaced.'
Assert-True (Test-DeploymentSubnetOverlap '192.168.88.0/24') 'Exact overlap was missed.'
Assert-True (-not (Test-DeploymentSubnetOverlap '0.0.0.0/0')) 'A default route is not an address conflict.'
Assert-True (-not (Test-DeploymentSubnetOverlap '10.20.30.0/24')) 'An unrelated LAN route must be preserved.'
Write-Host 'Windows B-side script: parse, DHCP preservation, persistent alias, idempotence, rollback and conflicts passed (mock commands).'
