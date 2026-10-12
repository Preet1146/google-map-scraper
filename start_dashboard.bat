@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
where %PY% >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this file again.
  pause
  exit /b 1
)

if not exist ".setup_done" (
  echo First run: installing the required packages. This takes a few minutes, please wait...
  %PY% -m pip install -r requirements.txt || goto :fail
  %PY% -m playwright install chromium || goto :fail
  echo done>.setup_done
)

%PY% dashboard.py
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Dashboard failed to start. Check the internet connection and that Python 3.10 or newer is installed.
echo To retry the package install, delete the file .setup_done and run this again.
pause
exit /b 1
