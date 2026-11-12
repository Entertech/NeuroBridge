@echo off
setlocal
if /i "%~1"=="--help" goto help
if "%~1"=="" goto help
if /i not "%~1"=="ssh" goto invalid
if not "%~3"=="" goto invalid
set "ssh_action="
if /i "%~2"=="enable" set "ssh_action=Enable"
if /i "%~2"=="status" set "ssh_action=Status"
if /i "%~2"=="disable" set "ssh_action=Disable"
if not defined ssh_action goto invalid

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\remote-support.ps1" -Action %ssh_action% -RequireGateway
exit /b %ERRORLEVEL%

:help
echo Usage: neurobridge ssh ^<enable^|status^|disable^>
exit /b 0

:invalid
echo Usage: neurobridge ssh ^<enable^|status^|disable^> 1>&2
exit /b 2
