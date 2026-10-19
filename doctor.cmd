@echo off
cd /d "%~dp0"
runtime\tooling\Scripts\python.exe scripts\doctor.py
pause
