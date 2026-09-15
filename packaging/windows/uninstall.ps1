$ErrorActionPreference = "Stop"
$install = Join-Path $env:ProgramFiles "NeuroBridge"
$python = Join-Path $install "runtime\python.exe"
if (Test-Path $python) {
  & $python (Join-Path $install "windows\service.py") stop
  & $python (Join-Path $install "windows\service.py") remove
}
Remove-Item -Recurse -Force $install
# Configuration, logs, and recordings remain under ProgramData for recovery.
