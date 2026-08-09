"""Channel performance report + audience insights feedback loop.

Reads every Short's stats from YouTube Studio (bot Firefox profile),
prints a report, asks the LLM what is working, and stores the winning
themes in .mp/audience_insights.json so topic generation can lean into
what the audience responds to.

Usage, from the project root (bot Firefox window must be closed):
    python scripts/channel_report.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager

import shutil

from cache import get_accounts
from config import get_ollama_model, get_summary_export_path
from llm_provider import select_model, generate_text

INSIGHTS_PATH = os.path.join(ROOT, ".mp", "audience_insights.json")
REPORT_PATH = os.path.join(ROOT, "logs", "channel_report.json")
DAILY_PATH = os.path.join(ROOT, "logs", "analytics_daily.json")

VISIBILITY_WORDS = ("Público", "Programado", "Privado", "Oculto", "Borrador")


def parse_number(text):
    text = text.strip().replace(" ", " ")
    match = re.match(r"^([\d.,]+)\s*(mil|M)?$", text)
    if not match:
        return None
    value = float(match.group(1).replace(".", "").replace(",", "."))
    if match.group(2) == "mil":
        value *= 1000
    elif match.group(2) == "M":
        value *= 1000000
    return int(value)


def open_studio_browser():
    account = get_accounts("youtube")[0]
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--width=1600")
    opts.add_argument("--height=1200")
    opts.add_argument("-profile")
    opts.add_argument(account["firefox_profile"])
    return webdriver.Firefox(
        service=Service(GeckoDriverManager().install()), options=opts
    )


def scrape_shorts(browser=None):
    own_browser = browser is None
    if own_browser:
        browser = open_studio_browser()
    videos = []
    try:
        browser.get("https://studio.youtube.com")
        time.sleep(8)
        channel_id = browser.current_url.split("/channel/")[-1].split("/")[0]
        browser.get(
            f"https://studio.youtube.com/channel/{channel_id}/videos/short"
        )
        rows = []
        for _ in range(10):
            time.sleep(5)
            rows = browser.find_elements(By.TAG_NAME, "ytcp-video-row")
            if rows:
                break

        for row in rows:
            lines = [l.strip() for l in row.text.split("\n") if l.strip()]
            if len(lines) < 2:
                continue
            entry = {"title": lines[1][:120]}
            for line in lines:
                if line in VISIBILITY_WORDS:
                    entry["visibility"] = line
                elif re.match(r"^\d{1,2} \w{3,4} \d{4}$", line):
                    entry["date"] = line
            numbers = [parse_number(l) for l in lines[3:]]
            numbers = [n for n in numbers if n is not None]
            if numbers:
                entry["views"] = numbers[0]
                entry["comments"] = numbers[-1] if len(numbers) > 1 else 0
            videos.append(entry)
    finally:
        if own_browser:
            browser.quit()
    return videos


# Etiquetas de la pestaña "Interacción" de un Short en Studio (español)
RETENTION_LABELS = ("se quedaron viendo",)
DURATION_LABELS = ("duración media",)


def _parse_duration_seconds(text):
    match = re.search(r"(\d+):(\d{2})", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _parse_retention_metrics(page_text):
    """Busca las métricas en el texto de la pestaña Interacción de un Short.

    Studio pinta la etiqueta y el valor en líneas contiguas, así que se
    busca el valor en una ventana de 3 líneas desde cada etiqueta.
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    out = {"retencion_pct": None, "dur_media_s": None}
    for i, line in enumerate(lines):
        low = line.lower()
        window = " ".join(lines[i : i + 3])
        if out["retencion_pct"] is None and any(k in low for k in RETENTION_LABELS):
            match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", window)
            if match:
                out["retencion_pct"] = float(match.group(1).replace(",", "."))
        if out["dur_media_s"] is None and any(k in low for k in DURATION_LABELS):
            out["dur_media_s"] = _parse_duration_seconds(window)
        if out["retencion_pct"] is not None and out["dur_media_s"] is not None:
            break
    return out


def _norm_title(text):
    return " ".join(re.sub(r"[\W_]+", " ", (text or "").lower()).split())[:60]


def _load_retention_map():
    """Título normalizado -> métricas de retención del último bucle diario."""
    try:
        with open(DAILY_PATH, encoding="utf-8") as fh:
            daily = json.load(fh)
    except Exception:
        return {}
    out = {}
    for v in daily.get("videos", []):
        if v.get("retencion_pct") is None and v.get("dur_media_s") is None:
            continue
        out[_norm_title(v.get("title"))] = {
            "retencion_pct": v.get("retencion_pct"),
            "dur_media_s": v.get("dur_media_s"),
        }
    return out


def scrape_video_retention(browser, video_id):
    browser.get(
        f"https://studio.youtube.com/video/{video_id}/analytics/tab-interest_viewers/period-default"
    )
    metrics = {"retencion_pct": None, "dur_media_s": None}
    for _ in range(6):
        time.sleep(4)
        page_text = browser.find_element(By.TAG_NAME, "body").text
        metrics = _parse_retention_metrics(page_text)
        if metrics["retencion_pct"] is not None or metrics["dur_media_s"] is not None:
            break
    return metrics


def main():
    print("[report] Leyendo el canal en Studio...")
    videos = scrape_shorts()
    public = [v for v in videos if v.get("visibility") == "Público"]
    scheduled = [v for v in videos if v.get("visibility") == "Programado"]

    print(f"\n===== INFORME DEL CANAL ({datetime.now():%d/%m/%Y %H:%M}) =====")
    print(f"Shorts públicos: {len(public)} | Programados: {len(scheduled)}")
    for v in sorted(public, key=lambda x: -(x.get("views") or 0)):
        print(
            f"  {v.get('views', '?'):>6} vistas | {v.get('comments', 0):>3} comentarios"
            f" | {v.get('date', '?'):>12} | {v['title'][:70]}"
        )

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    report = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "videos": videos,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    def export_report():
        export = get_summary_export_path().strip()
        if export:
            try:
                shutil.copy2(
                    REPORT_PATH,
                    os.path.join(os.path.dirname(export), "channel_report.json"),
                )
                print("[report] Informe exportado para el briefing")
            except Exception as e:
                print(f"[report] No se pudo exportar: {e}")

    if len(public) < 3:
        print("[report] Aún pocos vídeos públicos para extraer patrones.")
        export_report()
        return

    select_model(get_ollama_model())
    retention_map = _load_retention_map()

    def _row(v):
        base = (
            f"- {v.get('views', 0)} vistas, {v.get('comments', 0)} comentarios "
            f"({v.get('date', '?')}): {v['title']}"
        )
        metrics = retention_map.get(_norm_title(v["title"]))
        if metrics and metrics["retencion_pct"] is not None:
            base += f" [retención {metrics['retencion_pct']}%"
            if metrics["dur_media_s"] is not None:
                base += f", dur. media {metrics['dur_media_s']}s"
            base += "]"
        return base

    table = "\n".join(_row(v) for v in public)
    analysis = generate_text(
        "Eres el analista de un canal de YouTube Shorts español de videojuegos. "
        "Con estos datos de rendimiento por vídeo, responde EN ESPAÑOL:\n"
        "1) Qué patrones separan los vídeos con más vistas y más retención "
        "de los demás (tema, formato del título, franquicia, duración).\n"
        "2) Tres recomendaciones concretas de contenido.\n"
        "3) Una lista JSON al final con este formato exacto: "
        '{"temas_ganadores": ["...", "..."]} con 3-5 temáticas a potenciar.\n\n'
        + table,
        think=True,
    )
    print("\n===== ANÁLISIS DEL LLM =====")
    print(analysis)

    report["analysis"] = analysis
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    export_report()

    match = re.search(r'\{\s*"temas_ganadores"\s*:\s*\[.*?\]\s*\}', analysis, re.DOTALL)
    if match:
        try:
            insights = json.loads(match.group(0))
            insights["updated"] = datetime.now().isoformat(timespec="minutes")
            with open(INSIGHTS_PATH, "w", encoding="utf-8") as fh:
                json.dump(insights, fh, ensure_ascii=False, indent=2)
            print(f"\n[report] Temas ganadores guardados en {INSIGHTS_PATH}")
        except Exception as e:
            print(f"[report] No se pudieron guardar los insights: {e}")


if __name__ == "__main__":
    main()
