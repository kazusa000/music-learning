@echo off
cd /d "%~dp0"
runtime\tooling\Scripts\python.exe scripts\app.py start --open
if errorlevel 1 pause
