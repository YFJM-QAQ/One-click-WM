@echo off
chcp 65001 >nul

cd /d "%~dp0"

copy /Y .\logo.png .\bin\logo\logo.png >nul
copy /Y .\Setting.ini .\bin\Setting.ini >nul

"%~dp0bin\Running.bat" "%~dp0bin\Setting.ini" "%~dp0bin\Setting_system.ini"
