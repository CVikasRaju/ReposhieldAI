@echo off
setlocal enabledelayedexpansion
title RepoShield AI - Gateway ^& Dashboard

echo ======================================================================
echo                      RepoShield AI - Zero-Trust Shield
echo ======================================================================
echo.

:: 1. Check Python virtual environment
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [!] Virtual environment .venv not found.
    echo [*] Checking system python...
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Python is not installed or not on PATH. Please install Python 3.11+.
        pause
        exit /b 1
    )
    set "PY=python"
)

:: 2. Set default approver token if not already defined
if "%RS_APPROVER_TOKEN%"=="" (
    set "RS_APPROVER_TOKEN=reposhield-approver-token"
)

:: 3. Ensure .aishield exists (auto-initializes if missing)
if not exist ".aishield" (
    echo [*] Initializing default .aishield policy file...
    "%PY%" -m backend.main --init .
)

:: 4. Start Gateway in background window
echo [*] Starting RepoShield Gateway (MCP :8001 / API :8000)...
start "RepoShield Gateway" cmd /c "title RepoShield Gateway && set RS_APPROVER_TOKEN=%RS_APPROVER_TOKEN% && "%PY%" -m backend.main"

:: 5. Start Dashboard in background window
echo [*] Starting RepoShield Web Dashboard (:5173)...
start "RepoShield Dashboard" cmd /c "title RepoShield Dashboard && cd frontend && npm run dev"

:: 6. Wait a few seconds for services to initialize
timeout /t 3 /nobreak >nul

:: 7. Open browser automatically
echo [*] Launching dashboard in browser...
start http://localhost:5173/#token=%RS_APPROVER_TOKEN%

echo.
echo ======================================================================
echo  STATUS: RepoShield AI is active and protecting your workspace!
echo ======================================================================
echo.
echo   - Web Dashboard: http://localhost:5173/#token=%RS_APPROVER_TOKEN%
echo   - MCP Endpoint:  http://127.0.0.1:8001/mcp
echo   - Approver Token: %RS_APPROVER_TOKEN%
echo.
echo  Connect your AI Assistant (Cursor / Claude / Cline) to:
echo  http://127.0.0.1:8001/mcp
echo.
echo ======================================================================
echo  Press any key to stop all RepoShield services...
pause >nul

echo [*] Stopping services...
taskkill /fi "WINDOWTITLE eq RepoShield Gateway*" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq RepoShield Dashboard*" /f >nul 2>&1

echo [✓] RepoShield stopped cleanly.
timeout /t 2 /nobreak >nul
