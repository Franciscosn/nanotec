@echo off
setlocal
cd /d "%~dp0python_rewrite"
py -3 nanotec_motor_app.py
if errorlevel 1 (
  python nanotec_motor_app.py
)
endlocal
