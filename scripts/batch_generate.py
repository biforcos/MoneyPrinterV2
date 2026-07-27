"""Batch mode: generate and upload Shorts in a loop using local models.

Runs until stopped (Ctrl+C) or until --max videos have been produced.
Enforces the local image provider (ComfyUI) and starts its server if it
is not already running.

Usage, from the project root:
    python scripts/batch_generate.py            # loop until Ctrl+C
    python scripts/batch_generate.py --max 5    # stop after 5 videos
    python scripts/batch_generate.py --max 5 --delay 300  # 5 min between videos
    python scripts/batch_generate.py --no-upload          # generate only
"""

import argparse
import atexit
import ctypes
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import requests

from cache import get_accounts
from classes.Tts import TTS
from classes.YouTube import YouTube
from config import get_comfyui_base_url, get_image_provider, get_ollama_base_url, get_ollama_model
from llm_provider import select_model
from utils import rem_temp_files


LOCK_PATH = os.path.join(ROOT, ".mp", "batch_lock.json")


def acquire_single_instance_lock() -> None:
    """
    Two batches sharing .mp/ delete each other's temp files, so only one
    may run. The lock is a JSON file (survives rem_temp_files) holding
    the owner PID; a stale lock from a dead process is reclaimed.
    """
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH, "r", encoding="utf-8") as f:
                owner_pid = int(json.load(f)["pid"])
        except Exception:
            owner_pid = None
        if owner_pid:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, owner_pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                raise SystemExit(
                    f"Ya hay un batch corriendo (PID {owner_pid}). "
                    "Solo puede haber uno a la vez: comparten los temporales de .mp/."
                )
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid()}, f)
    atexit.register(
        lambda: os.path.exists(LOCK_PATH) and os.remove(LOCK_PATH)
    )


def ensure_local_providers() -> None:
    if get_image_provider() != "comfyui":
        raise SystemExit(
            "image_provider no es 'comfyui'. Este modo exige generación local "
            "de imágenes: cambia image_provider en config.json."
        )

    model = get_ollama_model()
    if not model:
        raise SystemExit("ollama_model está vacío en config.json.")
    try:
        requests.get(f"{get_ollama_base_url()}/api/tags", timeout=5)
    except Exception:
        raise SystemExit("Ollama no responde. Arranca Ollama primero.")
    select_model(model)

    comfy = get_comfyui_base_url().rstrip("/")
    try:
        requests.get(f"{comfy}/system_stats", timeout=3)
        print("[batch] ComfyUI ya está corriendo.")
        return
    except Exception:
        pass

    print("[batch] Arrancando ComfyUI...")
    os.startfile(os.path.join(ROOT, "scripts", "start_comfyui.bat"))
    for _ in range(24):
        time.sleep(5)
        try:
            requests.get(f"{comfy}/system_stats", timeout=3)
            print("[batch] ComfyUI listo.")
            return
        except Exception:
            continue
    raise SystemExit("ComfyUI no ha arrancado tras 2 minutos.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera y sube Shorts en bucle")
    parser.add_argument("--max", type=int, default=0, help="máximo de vídeos (0 = sin límite)")
    parser.add_argument("--delay", type=int, default=0, help="segundos de espera entre vídeos")
    parser.add_argument("--no-upload", action="store_true", help="solo generar, no subir")
    args = parser.parse_args()

    acquire_single_instance_lock()
    ensure_local_providers()

    accounts = get_accounts("youtube")
    if not accounts:
        raise SystemExit("No hay cuentas de YouTube configuradas (crea una en la app).")
    account = accounts[0]
    print(f"[batch] Cuenta: {account['nickname']} | nicho: {account['niche']}")

    tts = TTS()
    produced, uploaded, consecutive_failures = 0, 0, 0

    try:
        while args.max <= 0 or produced < args.max:
            iteration = produced + 1
            print(f"\n[batch] ===== Vídeo {iteration}{f'/{args.max}' if args.max > 0 else ''} =====")
            rem_temp_files()
            try:
                youtube = YouTube(
                    account["id"],
                    account["nickname"],
                    account["firefox_profile"],
                    account["niche"],
                    account["language"],
                )
                youtube.generate_video(tts)
                produced += 1

                if args.no_upload:
                    print(f"[batch] Generado (sin subir): {youtube.video_path}")
                else:
                    if youtube.upload_video():
                        uploaded += 1
                        print(f"[batch] Subido: {youtube.uploaded_video_url}")
                    else:
                        print("[batch] La subida falló; el vídeo queda en videos/.")
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                print(f"[batch] Error en la iteración: {e}")
                if consecutive_failures >= 3:
                    print("[batch] 3 fallos seguidos; abortando para no quemar recursos.")
                    break

            if args.delay > 0 and (args.max <= 0 or produced < args.max):
                print(f"[batch] Esperando {args.delay}s...")
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[batch] Detenido por el usuario.")

    print(f"\n[batch] Resumen: {produced} generados, {uploaded} subidos.")
    print("[batch] Copias permanentes en videos/.")


if __name__ == "__main__":
    main()
