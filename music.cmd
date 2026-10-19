@echo off
cd /d "%~dp0"
runtime\tooling\Scripts\python.exe scripts\music_cli.py %*
