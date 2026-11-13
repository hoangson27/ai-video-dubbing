@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-dev.ps1" %*
exit /b %errorlevel%
