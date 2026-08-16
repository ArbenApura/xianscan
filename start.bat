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

:: 4. SETUP WEB ENVIRONMENT & BUILD
if not exist "web\node_modules" (
    echo [*] Installing web application dependencies...
    cd web
    call npm install
    cd ..
)

if not exist "web\build\index.js" (
    echo [*] Production build not found. Building web application...
    cd web
    call npm run build
    cd ..
)

:: 5. FREE PORTS IF OLD INSTANCES ARE LINGERING
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8123" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>nul
)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8124" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>nul
)

echo.
echo ================================================================
echo   [+] Starting ML Sidecar on http://127.0.0.1:8123
echo   [+] Starting Web App on    http://localhost:8124
echo ================================================================
echo.

:: Launch ML Sidecar in background
start "XianScan ML Backend" /min cmd /c "cd /d "%~dp0ml" && ..\ml\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123"

cd /d "%~dp0web"
call npm run preview

:: CLEANUP ON TERMINATION (WHEN WEB APP STOPS)
echo.
echo [*] Shutting down XianScan services...
call "%~dp0stop.bat"

