@echo off
REM run-local.bat — delegates to run-local.ps1 for reliable WSL path handling.
REM Requires WSL2 with Ubuntu and PowerShell (both included in Windows 10/11).
powershell -ExecutionPolicy Bypass -File "%~dp0run-local.ps1"
