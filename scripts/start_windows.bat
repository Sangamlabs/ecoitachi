@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\activate.bat" (
  echo Run install_windows.bat first.
  pause
  exit /b 1
)
if not exist ".env" (
  echo .env is missing. Copy .env.example to .env and fill in your credentials.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
python bot.py
pause
