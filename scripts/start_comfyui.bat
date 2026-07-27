@echo off
REM Starts the local ComfyUI server used by the "comfyui" image provider.
REM Leave this window open while generating videos.
C:\Users\bifor\ComfyUI\venv\Scripts\python.exe C:\Users\bifor\ComfyUI\main.py --listen 127.0.0.1 --port 8188
