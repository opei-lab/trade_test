@echo off
chcp 65001 >nul 2>&1
title Stock Screener
cd /d "%~dp0"

echo ========================================
echo   Stock Screener
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    echo Run setup.bat first
    pause
    exit /b 1
)

REM Start Ollama in background if available
where ollama >nul 2>&1
if not errorlevel 1 (
    echo Starting Ollama...
    start /min ollama serve 2>nul
    timeout /t 3 /nobreak >nul
)

echo Starting... browser will open automatically
echo Close this window to stop the server
echo.
python -m streamlit run app.py

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start. Run setup.bat first.
)
pause
