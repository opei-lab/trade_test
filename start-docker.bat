@echo off
title Stock Screener (Docker)
cd /d "%~dp0"

echo Stock Screener を起動します (Docker)...
echo http://localhost:8501 でアクセスできます
echo 終了するには Ctrl+C を押してください
echo.
docker-compose up --build
