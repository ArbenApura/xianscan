@echo off
setlocal enabledelayedexpansion

echo ================================================================
echo   XIANSCAN -- ALL-IN-ONE AUTOMATED LAUNCHER
echo ================================================================
echo.

cd /d "%~dp0"

:: 1. CHECK PYTHON
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python 3 was not found in PATH. Please install Python 3.10+ from python.org.
    pause
    exit /b 1
)

:: 2. SETUP ML VIRTUAL ENVIRONMENT
if not exist "ml\.venv\Scripts\activate.bat" (
    echo [*] Creating Python virtual environment in ml\.venv...
    python -m venv ml\.venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [*] Installing dependencies with auto-hardware acceleration...
    ml\.venv\Scripts\python.exe -m pip install --upgrade pip
    ml\.venv\Scripts\python.exe -m pip install -r ml\requirements.txt
)

:: 3. DOWNLOAD ML WEIGHTS IF MISSING
if not exist "ml\models\comictextdetector.pt.onnx" (
    echo [*] Downloading required ML model weights...
    ml\.venv\Scripts\python.exe ml\scripts\download_models.py
)

:: 4. SETUP WEB ENVIRONMENT
if not exist "web\node_modules" (
    echo [*] Installing web application dependencies...
    cd web
    call npm install
    cd ..
)

echo.
echo ================================================================
echo   [+] Starting ML Sidecar on http://127.0.0.1:8001
echo   [+] Starting Web App on    http://localhost:5173
echo ================================================================
echo.

:: Launch ML Sidecar in background window and Web App in foreground
start "XianScan ML Backend" cmd /k "cd /d "%~dp0ml" && ..\ml\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload"

cd /d "%~dp0web"
call npm run dev
