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

# Under the Task Scheduler stdout is cp1252 and the status emojis kill
# every print; force UTF-8 with replacement so output can never crash us
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

from datetime import datetime

from cache import get_accounts
from classes.Tts import TTS
from classes.YouTube import YouTube
from config import (
    get_comfyui_base_url,
    get_image_provider,
    get_ollama_base_url,
    get_ollama_model,
    get_summary_export_path,
)
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
    for _ in range(60):
        time.sleep(5)
        try:
            requests.get(f"{comfy}/system_stats", timeout=3)
            print("[batch] ComfyUI listo.")
            return
        except Exception:
            continue
    raise SystemExit("ComfyUI no ha arrancado tras 5 minutos.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera y sube Shorts en bucle")
    parser.add_argument("--max", type=int, default=0, help="máximo de vídeos (0 = sin límite)")
    parser.add_argument("--delay", type=int, default=0, help="segundos de espera entre vídeos")
    parser.add_argument("--no-upload", action="store_true", help="solo generar, no subir")
    parser.add_argument(
        "--shutdown-comfyui",
        action="store_true",
        help="apaga el servidor ComfyUI al terminar (para ejecuciones nocturnas)",
    )
    parser.add_argument(
        "--news-only",
        action="store_true",
        help="genera solo mientras el siguiente tema pendiente sea una noticia",
    )
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
    started = datetime.now()
    results = []

    def next_pending_is_news():
        from datetime import timedelta

        try:
            with open(
                os.path.join(ROOT, "news_queue.json"), encoding="utf-8"
            ) as fh:
                for item in json.load(fh):
                    try:
                        age = datetime.now() - datetime.fromisoformat(
                            item.get("fecha", "")
                        )
                    except (TypeError, ValueError):
                        continue
                    if age <= timedelta(hours=20):
                        return True
        except Exception:
            pass
        return False

    try:
        while args.max <= 0 or produced < args.max:
            if args.news_only and not next_pending_is_news():
                print("[batch] No quedan noticias pendientes; fin del ciclo.")
                break
            iteration = produced + 1
            print(f"\n[batch] ===== Vídeo {iteration}{f'/{args.max}' if args.max > 0 else ''} =====")
            rem_temp_files()
            entry = {"status": "generation_failed"}
            iteration_start = datetime.now()
            try:
                generation_error = None
                for attempt in (1, 2):
                    youtube = YouTube(
                        account["id"],
                        account["nickname"],
                        account["firefox_profile"],
                        account["niche"],
                        account["language"],
                    )
                    try:
                        youtube.generate_video(tts)
                        generation_error = None
                        break
                    except Exception as gen_err:
                        generation_error = gen_err
                        # Put the consumed topic back and retry once:
                        # most failures are transient hiccups
                        youtube.restore_consumed_topic()
                        if attempt == 1:
                            print(f"[batch] Generación falló, reintento en 30s: {gen_err}")
                            time.sleep(30)
                if generation_error is not None:
                    raise generation_error
                produced += 1
                entry.update(
                    topic=getattr(youtube, "subject", ""),
                    title=youtube.metadata.get("title", ""),
                    file=os.path.basename(youtube.video_path),
                    news=bool(getattr(youtube, "_news_immediate", False)),
                    status="generated",
                )

                if args.no_upload:
                    print(f"[batch] Generado (sin subir): {youtube.video_path}")
                else:
                    if youtube.upload_video():
                        uploaded += 1
                        entry["status"] = "uploaded"
                        entry["url"] = youtube.uploaded_video_url
                        slot = getattr(youtube, "scheduled_for", None)
                        if slot:
                            entry["scheduled_for"] = slot.isoformat(timespec="minutes")
                        print(f"[batch] Subido: {youtube.uploaded_video_url}")
                    else:
                        entry["status"] = "upload_failed"
                        print("[batch] La subida falló; el vídeo queda en videos/.")
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                entry["error"] = str(e)
                print(f"[batch] Error en la iteración: {e}")
                if consecutive_failures >= 3:
                    print("[batch] 3 fallos seguidos; abortando para no quemar recursos.")
                    break
            finally:
                entry["minutes"] = round(
                    (datetime.now() - iteration_start).total_seconds() / 60, 1
                )
                results.append(entry)

            if args.delay > 0 and (args.max <= 0 or produced < args.max):
                print(f"[batch] Esperando {args.delay}s...")
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[batch] Detenido por el usuario.")
    finally:
        # Machine-readable summary for external reporting (Telegram
        # briefings etc.): always written, even after a crash
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        summary = {
            "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now().isoformat(timespec="seconds"),
            "account": account["nickname"],
            "produced": produced,
            "uploaded": uploaded,
            "videos": results,
        }
        with open(
            os.path.join(ROOT, "logs", "last_run.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        with open(
            os.path.join(ROOT, "logs", "history.jsonl"), "a", encoding="utf-8"
        ) as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")

        # Optional extra copy for external assistants (e.g. into WSL).
        # Best-effort: an unreachable destination must never kill the batch.
        export_path = get_summary_export_path().strip()
        if export_path:
            try:
                import shutil

                shutil.copy2(
                    os.path.join(ROOT, "logs", "last_run.json"), export_path
                )
                print(f"[batch] Resumen exportado a {export_path}")
            except Exception as e:
                print(f"[batch] No se pudo exportar el resumen: {e}")

    print(f"\n[batch] Resumen: {produced} generados, {uploaded} subidos.")
    print("[batch] Copias permanentes en videos/ y resumen en logs/last_run.json.")

    if args.shutdown_comfyui:
        print("[batch] Apagando ComfyUI...")
        import subprocess

        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -like '*ComfyUI*main.py*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }",
            ],
            check=False,
        )


if __name__ == "__main__":
    main()
