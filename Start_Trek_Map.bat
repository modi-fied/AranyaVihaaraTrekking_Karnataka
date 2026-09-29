@echo off
cd /d "%~dp0"
python trek_map.py
if errorlevel 1 py trek_map.py
pause
