@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install Python 3.11+ from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" during installation.
  pause
  exit /b 1
)

echo [2/4] Creating virtual environment...
python -m venv .venv

echo [3/4] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo [4/4] Preparing .env...
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - EDIT IT now with your API_ID, API_HASH, BOT_TOKEN and OWNER_ID.
) else (
  echo .env already exists - keeping it.
)

echo.
echo Setup complete. Run start_windows.bat to launch the bot.
pause
