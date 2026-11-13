@echo off
title GetModAndroid
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nahi mila. https://www.python.org/downloads/ se install karo aur "Add Python to PATH" tick karo.
  pause
  exit /b 1
)
if not exist venv\Scripts\python.exe (
  echo Pehli baar setup ho raha hai, thoda wait karo...
  python -m venv venv
)
call venv\Scripts\activate
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Packages install nahi hue. Internet check karo.
  pause
  exit /b 1
)
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000/"
echo.
echo Site: http://127.0.0.1:5000/   Admin: http://127.0.0.1:5000/admin/
echo Band karne ke liye is window me Ctrl+C dabao.
python run.py
pause
