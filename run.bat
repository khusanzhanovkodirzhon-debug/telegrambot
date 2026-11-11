@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo Создаю виртуальное окружение...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python bot.py
pause
