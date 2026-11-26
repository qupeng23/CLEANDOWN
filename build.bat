@echo off
chcp 65001 >nul
pyinstaller --onefile --noconsole --name CLEANDOWN cleandown.py
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Built: dist\CLEANDOWN.exe
