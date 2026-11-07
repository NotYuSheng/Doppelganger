@echo off
setlocal
rem Cross-platform setup for Windows (Linux/macOS users: run setup.sh).
rem Creates a venv, installs pinned dependencies, then opens the guided menu
rem (python -m doppelganger) to turn your chat exports into a training-ready
rem dataset.

set "BASE_DIR=%~dp0"
rem Python writes UTF-8 even when output is redirected (the banner/menu use Unicode).
set "PYTHONUTF8=1"
cd /d "%BASE_DIR%"

echo [1/4] Creating virtual environment (venv)...
python -m venv venv
if errorlevel 1 (echo Failed to create virtual environment. & exit /b 1)

echo [2/4] Installing dependencies (this can take a while)...
call venv\Scripts\python -m pip install --upgrade pip
if errorlevel 1 (echo Failed to upgrade pip. & exit /b 1)
call venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (echo Failed to install dependencies. & exit /b 1)

echo [3/4] Preparing .env...
if not exist ".env" (
    copy "example.env" ".env" >nul
    echo       Created .env from example.env - edit it to enable optional LLM features.
)

echo [4/4] Preparing your dataset...
if not exist "data" mkdir "data"
rem Guided menu: finds your exports in data\, asks who you are, builds the dataset.
call venv\Scripts\python -m doppelganger
if errorlevel 1 (echo The menu exited with an error. & exit /b 1)

echo.
echo Setup complete.
echo.
echo Run the menu any time (parse, audit, train, merge, chat):
echo.
echo   venv\Scripts\activate
echo   python -m doppelganger
echo.
echo See README.md for training, merging and inference.
pause
