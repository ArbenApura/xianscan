@echo off

echo ================================================================
echo   XIANSCAN -- TERMINATING ALL ACTIVE PROCESSES
echo ================================================================
echo.

:: 1. Free port 8123 (ML Sidecar)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8123" ^| findstr "LISTENING"') do (
    echo [*] Terminating ML Sidecar process PID %%a...
    taskkill /F /PID %%a >nul 2>nul
)

:: 2. Free port 8124 (Web Application)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8124" ^| findstr "LISTENING"') do (
    echo [*] Terminating Web Server process PID %%a...
    taskkill /F /PID %%a >nul 2>nul
)

:: 3. Clean any orphaned uvicorn or python child processes running from xianscan ml\.venv
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ml\.venv*python*' -or $_.CommandLine -like '*uvicorn*app.main:app*' } | ForEach-Object { Write-Host ('[*] Killing orphan Python process PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo.
echo   [+] All XianScan server processes and background workers terminated.
echo ================================================================
ping 127.0.0.1 -n 2 >nul
