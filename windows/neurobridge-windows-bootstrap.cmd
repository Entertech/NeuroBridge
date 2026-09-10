@echo off
setlocal
rem Process-scoped policy only. Never change the machine execution policy.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-windows-gateway.ps1" %*
set "gateway_exit=%ERRORLEVEL%"
if not "%gateway_exit%"=="0" pause
exit /b %gateway_exit%
