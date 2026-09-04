@echo off
setlocal
python "%~dp0rgs.py" %*
exit /b %ERRORLEVEL%
