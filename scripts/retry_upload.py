"""One-off: retry the YouTube upload for an already-generated video.

Recovers the script by transcribing the video's audio, generates fresh
metadata with the configured LLM, and drives the normal upload flow.

Usage: run from the project root:
    python scripts/retry_upload.py <path-to-mp4>
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cache import get_accounts
from classes.YouTube import YouTube
from llm_provider import select_model, generate_text


def transcribe(video_path: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(video_path, language="es")
    return " ".join(segment.text.strip() for segment in segments)


def main() -> None:
    video_path = os.path.abspath(sys.argv[1])
    if not os.path.exists(video_path):
        raise SystemExit(f"Video not found: {video_path}")

    print("Transcribiendo el video para recuperar el guion...")
    script_text = transcribe(video_path)
    print(f"Guion recuperado: {script_text[:200]}...")

    account = get_accounts("youtube")[0]
    select_model("qwen3:8b")

    title = generate_text(
        "Genera un título para un YouTube Short en español sobre este guion, "
        f"incluyendo hashtags. Devuelve solo el título, máximo 90 caracteres:\n{script_text}"
    )[:100]
    description = generate_text(
        "Genera una descripción breve en español para un YouTube Short con este guion. "
        f"Devuelve solo la descripción:\n{script_text}"
    )

    print(f"Título: {title}")

    youtube = YouTube(
        account["id"],
        account["nickname"],
        account["firefox_profile"],
        account["niche"],
        account["language"],
    )
    youtube.metadata = {"title": title, "description": description}
    youtube.video_path = video_path

    if youtube.upload_video():
        print(f"SUBIDO: {youtube.uploaded_video_url}")
    else:
        print("FALLO EN LA SUBIDA (traceback arriba)")


if __name__ == "__main__":
    main()
