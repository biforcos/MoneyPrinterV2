"""Re-uploads the upload_failed videos recorded in logs/last_run.json.

Uses each video's recorded title, generates a fresh description from its
topic, respects publish_mode (scheduling slots), and rewrites
logs/last_run.json (+ the configured export copy) with the final outcome.

Usage, from the project root:
    python scripts/reupload_failed.py
"""

import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from cache import get_accounts
from classes.YouTube import YouTube
from config import get_ollama_model, get_summary_export_path
from llm_provider import select_model, generate_text


def main() -> None:
    summary_path = os.path.join(ROOT, "logs", "last_run.json")
    with open(summary_path, "r", encoding="utf-8") as f:
        run = json.load(f)

    pending = [v for v in run["videos"] if v.get("status") == "upload_failed"]
    if not pending:
        print("No hay vídeos pendientes de subir.")
        return

    account = get_accounts("youtube")[0]
    select_model(get_ollama_model())

    recovered = 0
    for i, video in enumerate(pending):
        if i > 0:
            # Give the previous Firefox time to release the profile lock
            time.sleep(10)
        path = os.path.join(ROOT, "videos", video["file"])
        if not os.path.exists(path):
            print(f"[reupload] No existe {video['file']}, lo salto.")
            continue

        print(f"[reupload] Subiendo: {video['title']}")
        description = generate_text(
            "Genera una descripción breve en español para un YouTube Short "
            f'titulado "{video["title"]}" sobre: {video["topic"]}. '
            "Máximo 2 frases, después una pregunta breve invitando a comentar, "
            "y 2-3 hashtags en la última línea. Sin comillas ni markdown. "
            "Devuelve solo la descripción."
        )

        youtube = YouTube(
            account["id"],
            account["nickname"],
            account["firefox_profile"],
            account["niche"],
            account["language"],
        )
        youtube.metadata = {"title": video["title"], "description": description}
        youtube.video_path = os.path.abspath(path)
        if video.get("news"):
            # Preserve news-ness: publish immediately instead of queueing
            youtube.topic_context = "reupload-news"

        if youtube.upload_video():
            recovered += 1
            video["status"] = "uploaded"
            video["url"] = youtube.uploaded_video_url
            slot = getattr(youtube, "scheduled_for", None)
            if slot:
                video["scheduled_for"] = slot.isoformat(timespec="minutes")
            print(f"[reupload] OK -> {youtube.uploaded_video_url}")
        else:
            print(f"[reupload] FALLO en {video['file']} (sigue en videos/).")

    run["uploaded"] = run.get("uploaded", 0) + recovered
    run["recovered_by_reupload"] = recovered
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2)

    export_path = get_summary_export_path().strip()
    if export_path:
        try:
            shutil.copy2(summary_path, export_path)
            print(f"[reupload] Resumen actualizado exportado a {export_path}")
        except Exception as e:
            print(f"[reupload] No se pudo exportar el resumen: {e}")

    print(f"[reupload] Recuperados {recovered}/{len(pending)}.")


if __name__ == "__main__":
    main()
