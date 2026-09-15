@echo off
setlocal
rem Run beside diagnose-service.ps1 in the complete project checkout.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose-service.ps1"
set "gateway_exit=%ERRORLEVEL%"
echo.
pause
exit /b %gateway_exit%
