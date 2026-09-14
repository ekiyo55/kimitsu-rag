@echo off
cd /d "%~dp0"
set KIMITSU_DOCS=%~dp0documents_research
start "RAG app (research)" python run.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8000/
