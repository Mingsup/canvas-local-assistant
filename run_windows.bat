@echo off
setlocal
cd /d "%~dp0"

echo [Setup] Canvas Local Assistant v1.5 for Windows
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating Python virtual environment...
    py -3.9 -m venv .venv 2>nul
    if errorlevel 1 (
        python -m venv .venv
    )
)

call ".venv\Scripts\activate.bat"

echo [Setup] Upgrading pip...
python -m pip install --upgrade pip >nul

echo [Setup] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Setup failed while installing Python packages.
    pause
    exit /b 1
)

echo [Setup] Installing Playwright Chromium if needed...
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo Chromium installation failed.
    pause
    exit /b 1
)

echo.
echo [Run] Starting Canvas auto-refresh app...
echo.
python canvas_assistant.py

echo.
pause
