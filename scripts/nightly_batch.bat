@echo off
REM Nightly autopilot: generates videos and leaves them scheduled for the
REM coming days' publication slots. Invoked by the Windows Task Scheduler.
cd /d C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
REM TEMPORAL mientras las Funciones avanzadas del canal estan "Pendiente":
REM primero recuperar los 5 videos pendientes, y generar solo 2 nuevos para
REM no volver a agotar el limite diario. Restaurar a --max 5 (y quitar el
REM reupload) cuando YouTube apruebe la verificacion avanzada.
venv\Scripts\python.exe scripts\janitor.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\daily_analytics.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\news_harvester.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\reupload_failed.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\batch_generate.py --max 2 --shutdown-comfyui >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\pin_comments.py >> logs\nightly.log 2>&1
venv\Scripts\python.exe scripts\comments_check.py >> logs\nightly.log 2>&1
