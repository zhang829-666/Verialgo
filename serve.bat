@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Same interpreter resolution as run.bat: the Store python.exe is a 0-byte stub.
set "PY=C:\Users\Y9000P\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto nopython

"%PY%" -u web.py %*
pause
exit /b 0

:nopython
echo.
echo [ERROR] No usable Python interpreter found.
echo Tried: %PY%
echo Install Python 3.10+ or edit the PY path in this file.
echo.
pause
exit /b 1
