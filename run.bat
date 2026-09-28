@echo off
rem Start Bass Stem Studio and open http://127.0.0.1:8765/ in the default browser.
rem If it is already running, the browser is simply opened again.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv not found. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 (
  echo.
  echo Bass Stem Studio stopped with an error. See the messages above.
  pause
)
