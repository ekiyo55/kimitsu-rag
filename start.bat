@echo off
cd /d "%~dp0"
start "RAG app" python run.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8000/
