@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0remote-support.ps1"
set "support_exit=%ERRORLEVEL%"
echo.
pause
exit /b %support_exit%
