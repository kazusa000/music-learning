@echo off
cd /d "%~dp0..\.."
data\envs\app\Scripts\python.exe scripts\instruments\open_library.py
if errorlevel 1 pause
