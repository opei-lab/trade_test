@echo off
chcp 65001 >nul 2>&1
title Stock Screener Setup
cd /d "%~dp0"

echo ========================================
echo   Stock Screener - Auto Setup
echo ========================================
echo.

echo [1/4] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo [OK]
echo.

echo [2/4] Installing Python packages...
pip install -q streamlit yfinance pandas numpy plotly requests beautifulsoup4 sqlalchemy pyyaml schedule openpyxl xlrd
if errorlevel 1 (
    echo [ERROR] Package install failed
    pause
    exit /b 1
)
echo [OK]
echo.

echo [3/4] Checking Ollama...
where ollama >nul 2>&1
if errorlevel 1 (
    echo Ollama not found. Installing...
    powershell -Command "irm https://ollama.com/install.ps1 | iex"
    echo Waiting for Ollama to start...
    timeout /t 10 /nobreak >nul
)
echo [OK]
echo.

echo [4/4] Downloading LLM model (llama3)...
echo This may take a few minutes on first run...
ollama pull llama3 2>nul
if errorlevel 1 (
    echo [WARNING] Ollama model download failed. LLM features will use fallback mode.
) else (
    echo [OK]
)
echo.

echo ========================================
echo   Setup Complete!
echo ========================================
echo.
echo Run start.bat to launch the app.
echo.
pause
