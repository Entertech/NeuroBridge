param([switch]$UrlOnly)

$ErrorActionPreference = 'Stop'

function Get-GatewayWebEndpoint {
    $services = Get-CimInstance Win32_Service -Filter "Name='NeuroBridge' OR Name='NeuroBridgeProject'"
    $gateway = $services | Where-Object { $_.State -eq 'Running' -and $_.ProcessId -gt 0 } |
        Sort-Object Name | Select-Object -First 1
    if (-not $gateway) {
        throw 'No running NeuroBridge gateway service. Start the gateway and try again.'
    }

    # netstat can read listener PIDs from a standard Windows account.
    $ports = @(netstat.exe -ano -p tcp | ForEach-Object {
        if ($_ -match '^\s*TCP\s+127\.0\.0\.1:(\d+)\s+0\.0\.0\.0:0\s+\S+\s+(\d+)\s*$' -and
            [int]$Matches[2] -eq $gateway.ProcessId) {
            [int]$Matches[1]
        }
    })

    foreach ($port in $ports) {
        $url = "http://127.0.0.1:$port/"
        $request = [Net.HttpWebRequest]::Create($url + 'runtime-config.js')
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 800
        $request.ReadWriteTimeout = 800
        try {
            $response = $request.GetResponse()
            try {
                if ($response.StatusCode -ne 200 -or $response.ContentLength -lt 1 -or $response.ContentLength -gt 4096) { continue }
                $reader = New-Object IO.StreamReader($response.GetResponseStream())
                try { $config = $reader.ReadToEnd() } finally { $reader.Dispose() }
            } finally {
                $response.Dispose()
            }
        } catch [Net.WebException] {
            if ($_.Exception.Response) { $_.Exception.Response.Dispose() }
            continue
        }
        if ($config -match '(?m)^window\.NEUROBRIDGE_B_CLIENT_ENDPOINT = "ws://127\.0\.0\.1:(\d+)/' -and
            $ports -contains [int]$Matches[1]) {
            return [pscustomobject]@{ Url = $url; HttpPort = $port; WebSocketPort = [int]$Matches[1] }
        }
    }
    throw 'The running NeuroBridge gateway did not respond on its local web listener. Try again after startup.'
}

try {
    $endpoint = Get-GatewayWebEndpoint
    if ($UrlOnly) {
        Write-Output $endpoint.Url
        exit 0
    }
    if ($env:SSH_CONNECTION) {
        $connection = $env:SSH_CONNECTION -split '\s+'
        $address = $null
        $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $authority, $username = $account -split '\\', 2
        if ($authority -eq $env:COMPUTERNAME) { $account = $username }
        if ($connection.Count -ne 4 -or -not [Net.IPAddress]::TryParse($connection[2], [ref]$address) -or
            $connection[3] -notmatch '^[0-9]{1,5}$' -or [int]$connection[3] -lt 1 -or [int]$connection[3] -gt 65535 -or
            $account -notmatch '^[\p{L}\p{N}_.@-]+(?:\\[\p{L}\p{N}_.@-]+)?$') {
            throw 'Could not read the SSH connection address or Windows account. Run neurobridge web --url to see the gateway URL.'
        }
        $http = $endpoint.HttpPort
        $ws = $endpoint.WebSocketPort
        Write-Output 'Run this command on the computer where you want to open the browser:'
        Write-Output ('ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:{0}:127.0.0.1:{0} -L 127.0.0.1:{1}:127.0.0.1:{1} -p {2} -l "{3}" {4}' -f $http, $ws, $connection[3], $account, $address)
        Write-Output "Keep the SSH tunnel open, then open $($endpoint.Url)"
        exit 0
    }
    Start-Process $endpoint.Url
    Write-Output $endpoint.Url
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
