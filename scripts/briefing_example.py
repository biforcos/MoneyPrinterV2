"""Example: turn logs/last_run.json into a one-paragraph morning briefing.

Meant as a starting point to wire MoneyPrinter's nightly summary into an
external assistant (Telegram, TTS briefing, etc.). Prints Spanish prose.

Usage: python scripts/briefing_example.py
"""

import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_briefing() -> str:
    path = os.path.join(ROOT, "logs", "last_run.json")
    if not os.path.exists(path):
        return "Esta noche no se ejecutó la generación de vídeos."

    with open(path, "r", encoding="utf-8") as f:
        run = json.load(f)

    started = datetime.fromisoformat(run["started"])
    finished = datetime.fromisoformat(run["finished"])
    elapsed_min = round((finished - started).total_seconds() / 60)

    parts = [
        f"Esta noche la fábrica de vídeos generó {run['produced']} vídeos "
        f"en {elapsed_min} minutos y subió {run['uploaded']}."
    ]

    scheduled = [v for v in run["videos"] if v.get("scheduled_for")]
    if scheduled:
        slots = ", ".join(
            datetime.fromisoformat(v["scheduled_for"]).strftime("%H:%M del %d/%m")
            for v in scheduled
        )
        parts.append(f"Se publicarán a las {slots}.")

    titles = [v["title"] for v in run["videos"] if v.get("title")]
    if titles:
        parts.append("Títulos: " + "; ".join(titles) + ".")

    failures = [v for v in run["videos"] if "failed" in v.get("status", "")]
    if failures:
        parts.append(
            f"Atención: {len(failures)} con problemas "
            f"({', '.join(v.get('status', '?') for v in failures)}). "
            "Los vídeos no subidos están salvados en videos/."
        )
    else:
        parts.append("Sin errores.")

    if run.get("sample"):
        parts.append("(Datos de ejemplo, aún no ha corrido ninguna noche real.)")

    return " ".join(parts)


if __name__ == "__main__":
    print(build_briefing())
