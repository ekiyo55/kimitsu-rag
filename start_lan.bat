@echo off
cd /d "%~dp0"
rem 同じ社内LANの他のPCからも使えるように待ち受ける（インターネットには公開しない）
set KIMITSU_HOST=0.0.0.0
echo このPCのアドレス（同僚にはこのアドレスの :8000 を伝える）:
ipconfig | findstr /i "IPv4"
start "RAG app (LAN)" python run.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8000/
