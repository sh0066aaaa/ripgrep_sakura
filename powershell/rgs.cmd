@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rgs.ps1" %*
exit /b %ERRORLEVEL%
