@echo off
setlocal
title NeuroBridge - Configure B-side Ethernet IP
if not exist "%~dp0configure-wired-ip.ps1" (
  echo Missing configure-wired-ip.ps1. Extract the complete ZIP before running.
  pause
  exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure-wired-ip.ps1"
if errorlevel 1 (
  echo Configuration failed or administrator permission was cancelled.
  pause
  exit /b 1
)
endlocal
