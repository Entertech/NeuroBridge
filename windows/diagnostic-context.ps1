# Read-only version/environment collection; does not need a working gateway.
function Get-NeuroBridgeIdentity {
    param([string]$Root)
    $version = 'unknown'
    $commit = 'unknown'
    if ($Root) {
        $registry = Join-Path $Root 'neurobridge\version_registry.toml'
        if (Test-Path -LiteralPath $registry -PathType Leaf) {
            $text = [string](Get-Content -LiteralPath $registry -Raw -Encoding UTF8 -ErrorAction SilentlyContinue)
            $section = [regex]::Match($text, '(?ms)^\[application\]\s*\r?\n(.*?)(?=^\[|\z)')
            $match = [regex]::Match($section.Groups[1].Value, '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
            if ($match.Success) { $version = $match.Groups[1].Value }
        }
        $info = Join-Path $Root 'build-info.txt'
        if (Test-Path -LiteralPath $info -PathType Leaf) {
            foreach ($line in Get-Content -LiteralPath $info -Encoding UTF8 -ErrorAction SilentlyContinue) {
                if ($version -eq 'unknown' -and $line -match '^application_version=([0-9]+\.[0-9]+\.[0-9]+)$') { $version = $Matches[1] }
                if ($line -match '^source_commit=([0-9a-f]{40})$') { $commit = $Matches[1] }
            }
        }
        if ($commit -eq 'unknown' -and (Test-Path -LiteralPath (Join-Path $Root '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
            try {
                $value = & git -C $Root rev-parse HEAD 2>$null
                if ($LASTEXITCODE -eq 0 -and $value -match '^[0-9a-f]{40}$') { $commit = [string]$value }
            } catch { }
        }
    }
    return [pscustomobject]@{ ApplicationVersion = $version; SourceCommit = $commit }
}

function Get-NeuroBridgeDiagnosticContext {
    param(
        [ValidateSet('installation', 'runtime')][string]$Scope,
        [string]$ApplicationRoot,
        [string]$PackageRoot,
        [string]$Python
    )
    $installed = Get-NeuroBridgeIdentity -Root $ApplicationRoot
    $package = Get-NeuroBridgeIdentity -Root $PackageRoot
    $caption = $osVersion = $osBuild = $osArchitecture = 'unknown'
    $osError = ''
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $caption = [string]$os.Caption
        $osVersion = [string]$os.Version
        $osBuild = [string]$os.BuildNumber
        $osArchitecture = [string]$os.OSArchitecture
    } catch { $osError = $_.Exception.Message }
    $nativeArchitecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $nativeArchitecture) { $nativeArchitecture = $env:PROCESSOR_ARCHITECTURE }
    if (-not $nativeArchitecture) { $nativeArchitecture = 'unknown' }
    $pythonVersion = $pythonArchitecture = 'unknown'
    if ($Python -and (Test-Path -LiteralPath $Python -PathType Leaf)) {
        try {
            $raw = & $Python -c "import json,platform,struct; print(json.dumps({'version':platform.python_version(),'architecture':str(struct.calcsize('P')*8)+'-bit'}))" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $value = $raw | ConvertFrom-Json
                if ($value.version) { $pythonVersion = [string]$value.version }
                if ($value.architecture -in '32-bit', '64-bit') { $pythonArchitecture = [string]$value.architecture }
            }
        } catch { }
    }
    return [pscustomobject][ordered]@{
        schemaVersion = 1
        diagnosticScope = $Scope
        generatedAtUtc = [DateTime]::UtcNow.ToString('o')
        applicationVersion = $installed.ApplicationVersion
        sourceCommit = $installed.SourceCommit
        versionBasis = 'application files on disk; service status is recorded separately'
        packageApplicationVersion = $package.ApplicationVersion
        packageSourceCommit = $package.SourceCommit
        osName = $caption
        osVersion = $osVersion
        osBuild = $osBuild
        osArchitecture = $osArchitecture
        nativeArchitecture = $nativeArchitecture
        osBits = $(if ([Environment]::Is64BitOperatingSystem) { 64 } else { 32 })
        exporterBits = $(if ([Environment]::Is64BitProcess) { 64 } else { 32 })
        powerShellVersion = $PSVersionTable.PSVersion.ToString()
        pythonVersion = $pythonVersion
        pythonArchitecture = $pythonArchitecture
        environmentQueryError = $osError
    }
}
