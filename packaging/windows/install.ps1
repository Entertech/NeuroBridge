$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this installer from an elevated PowerShell prompt."
}
$source = $PSScriptRoot
$install = Join-Path $env:ProgramFiles "NeuroBridge"
$data = Join-Path $env:ProgramData "NeuroBridge"
$sourcePython = Join-Path $source "payload\runtime\python.exe"
$sourceBridge = Join-Path $source "payload\runtime\neurobridge_affective_bridge.exe"
if (-not (Test-Path $sourcePython) -or -not (Test-Path $sourceBridge)) {
  throw "Candidate runtime is incomplete; python.exe and neurobridge_affective_bridge.exe are required."
}
New-Item -ItemType Directory -Force -Path $install, $data, (Join-Path $data "recordings"), (Join-Path $data "logs") | Out-Null
Copy-Item -Recurse -Force (Join-Path $source "payload\*") $install
if (-not (Test-Path (Join-Path $data "gateway.toml"))) {
  Copy-Item (Join-Path $source "gateway.toml.example") (Join-Path $data "gateway.toml")
}
$python = Join-Path $install "runtime\python.exe"
$gatewayConfig = Join-Path $data "gateway.toml"
& $python (Join-Path $install "neurobridge\configuration\migration.py") $gatewayConfig `
  --backup-directory (Join-Path $data "config-backups") `
  --history-path (Join-Path $data "config-migration-history.jsonl")
& $python (Join-Path $install "windows\service.py") --startup auto install
& $python (Join-Path $install "windows\service.py") start
