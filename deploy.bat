@echo off
echo Installing paramiko (SSH library)...
pip install paramiko --quiet
echo.
echo Running Memomatic deploy...
python "%~dp0deploy.py"
echo.
pause
