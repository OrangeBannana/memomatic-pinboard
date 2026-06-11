@echo off
REM run-local.bat — start Memomatic Pinboard in WSL2 from Windows.
REM
REM Requires WSL2 with Ubuntu (or any distro that has Python 3 available).
REM Launches run-local.sh inside WSL so the Linux environment matches the Pi.
REM
REM Usage:  double-click run-local.bat  or run from a Command Prompt / PowerShell.
REM         Then open http://127.0.0.1:8080/admin in your browser (token: dev).

setlocal

REM Strip trailing backslash from %~dp0 before passing to wslpath
set "WINDIR=%~dp0"
if "%WINDIR:~-1%"=="\" set "WINDIR=%WINDIR:~0,-1%"

REM usebackq lets us use backticks for the command, avoiding quote conflicts
for /f "usebackq delims=" %%i in (`wsl wslpath -u "%WINDIR%"`) do set "WSLDIR=%%i"

if "%WSLDIR%"=="" (
  echo ERROR: Could not convert path to WSL format. Is WSL installed?
  pause
  exit /b 1
)

wsl bash "%WSLDIR%/run-local.sh"
pause
endlocal
