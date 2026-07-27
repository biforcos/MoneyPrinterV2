@echo off
REM Nightly autopilot: generates videos and leaves them scheduled for the
REM coming days' publication slots. Invoked by the Windows Task Scheduler.
cd /d C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2
if not exist logs mkdir logs
venv\Scripts\python.exe scripts\batch_generate.py --max 5 --shutdown-comfyui >> logs\nightly.log 2>&1
