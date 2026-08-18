@echo off
REM Weekly channel analysis (Sundays): stats + LLM patterns + winning
REM themes, exported for the morning briefing.
cd /d C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
if not exist logs mkdir logs
venv\Scripts\python.exe scripts\channel_report.py >> logs\weekly_report.log 2>&1
