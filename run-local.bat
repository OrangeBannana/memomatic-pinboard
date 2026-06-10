@echo off
REM run-local.bat — start Memomatic Pinboard in WSL2 from Windows.
REM
REM Requires WSL2 with Ubuntu (or any distro that has Python 3 available).
REM Launches run-local.sh inside WSL so the Linux environment matches the Pi.
REM
REM Usage:  double-click run-local.bat  or run from a Command Prompt / PowerShell.
REM         Then open http://127.0.0.1:8080/admin in your browser (token: dev).

setlocal

REM Convert Windows path to a WSL path (e.g. C:\Users\... → /mnt/c/Users/...)
for /f "delims=" %%i in ('wsl wslpath -u "%~dp0"') do set WSL_DIR=%%i

wsl sh "%WSL_DIR%/run-local.sh"

endlocal
