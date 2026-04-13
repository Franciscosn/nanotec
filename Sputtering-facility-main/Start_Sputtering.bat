@echo off
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
  goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
  python run.py %*
  goto :eof
)

echo Python wurde nicht gefunden. Bitte Python installieren.
pause
