# Assemble the installable Windows candidate runtime on the CI runner.
# The directory must contain python.exe and neurobridge_affective_bridge.exe
# directly; install.ps1 rejects any other layout.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "artifacts\windows-runtime"
$PythonVersion = "3.11.9"
$PythonSha256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/pip/3.11/get-pip.py"

if (Test-Path $Runtime) {
  Remove-Item -Recurse -Force $Runtime
}
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

$archive = Join-Path $env:RUNNER_TEMP "python-embed.zip"
if (-not $env:RUNNER_TEMP) {
  $archive = Join-Path $env:TEMP "python-embed.zip"
}
Write-Host "Downloading Python $PythonVersion embeddable package"
Invoke-WebRequest -Uri $EmbedUrl -OutFile $archive
$hash = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash.ToLowerInvariant()
if ($hash -ne $PythonSha256) {
  throw "Python embeddable package SHA-256 mismatch: $hash"
}
Expand-Archive -Path $archive -DestinationPath $Runtime -Force

$pth = Get-ChildItem -Path $Runtime -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) {
  throw "Embeddable Python path file was not found"
}
$lines = Get-Content -Path $pth.FullName | ForEach-Object {
  if ($_ -match '^\s*#\s*import site\s*$') { "import site" } else { $_ }
}
if ($lines -notcontains "Lib\site-packages") {
  $lines += "Lib\site-packages"
}
Set-Content -Path $pth.FullName -Value $lines -Encoding ascii

$python = Join-Path $Runtime "python.exe"
$getPip = Join-Path $Runtime "get-pip.py"
Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPip
& $python $getPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed: $LASTEXITCODE" }
& $python -m pip install --no-warn-script-location -r (Join-Path $Root "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "dependency installation failed: $LASTEXITCODE" }
& $python -c "import serial, websockets, win32serviceutil"
if ($LASTEXITCODE -ne 0) { throw "embedded runtime cannot import required modules" }
Remove-Item -Force $getPip

$bridge = Join-Path $Runtime "neurobridge_affective_bridge.exe"
& $python (Join-Path $Root "windows\algorithm_build.py") --output $bridge
if ($LASTEXITCODE -ne 0) { throw "algorithm build failed: $LASTEXITCODE" }
if (-not (Test-Path $python) -or -not (Test-Path $bridge)) {
  throw "Windows candidate runtime is incomplete"
}
Write-Host "Windows candidate runtime ready: $Runtime"
