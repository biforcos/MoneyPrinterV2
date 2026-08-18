@echo off
REM Nightly autopilot: generates videos and leaves them scheduled for the
REM coming days' publication slots. Invoked by the Windows Task Scheduler.
cd /d C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
if not exist logs mkdir logs
REM Funciones avanzadas aprobadas en ambos canales (2026-08-08): sin limite
REM diario que esquivar. El --max 2 ES + 1 EN es eleccion deliberada de
REM calidad sobre volumen tras la caida de distribucion del 3-7 ago;
REM revisar al alza cuando el A/B de daily_analytics muestre recuperacion.
venv\Scripts\python.exe scripts\janitor.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\daily_analytics.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\news_harvester.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\batch_generate.py --max 2 >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\batch_generate.py --max 1 --account theBig4EN --shutdown-comfyui >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\pin_comments.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\comments_check.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\related_videos.py >> logs\nightly.log 2>&1
