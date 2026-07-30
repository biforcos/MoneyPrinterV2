@echo off
REM Daytime news cycle: harvest fresh gaming news and publish them right
REM away (news-only). Invoked by the Windows Task Scheduler several times
REM a day so news never waits for the nightly batch.
cd /d C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
venv\Scripts\python.exe scripts\news_harvester.py >> logs\news_cycle.log 2>&1
venv\Scripts\python.exe scripts\batch_generate.py --max 2 --news-only --shutdown-comfyui >> logs\news_cycle.log 2>&1
