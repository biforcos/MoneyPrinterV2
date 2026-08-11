"""Rellena el campo "Vídeo relacionado" de los Shorts recientes.

Barrido nocturno con el perfil bot: para cada vídeo ES de los últimos 14
días sin relacionado, elige otro vídeo del canal (misma franquicia por
solape de título; si no hay, el mejor retenido) y lo fija en Studio.
Estado en .mp/related_done.json para no repetir trabajo.

Uso, desde la raíz del proyecto (ventana del Firefox bot cerrada):
    python scripts/related_videos.py [--max 8]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from selenium.webdriver.common.by import By

from cache import get_accounts
from channel_report import open_studio_browser

DONE_PATH = os.path.join(ROOT, ".mp", "related_done.json")
HISTORY_PATH = os.path.join(ROOT, "logs", "retention_history.jsonl")
MAX_AGE_DAYS = 14

# Palabras que comparten casi todos los títulos del canal: no son franquicia
STOPWORDS = {
    "esto", "estos", "esta", "estas", "para", "como", "sabias", "nadie",
    "dice", "dicen", "sobre", "juego", "juegos", "videojuegos", "descubre",
    "revela", "nueva", "nuevo", "mejores", "mejor", "anos", "aqui", "todo",
    "todos", "gratis", "gratuita", "gratuito", "secreto", "secretos",
    "record", "records", "trailer", "youtube", "cambio", "cambia",
    "cambiara", "cambian", "verdad", "historia", "oculta", "oculto",
    "regresa", "vuelve", "llega", "podria", "puede", "hacer", "tiene",
}


def _words(text):
    text = (text or "").lower().translate(str.maketrans("áéíóúü", "aeiouu"))
    return {
        w
        for w in re.findall(r"[a-z0-9]+", text)
        if len(w) >= 4 and w not in STOPWORDS
    }


def _video_id(video):
    match = re.search(r"[?&]v=([\w-]{11})", video.get("url") or "")
    return match.group(1) if match else None


def choose_related(video, candidates, retention_best):
    """El candidato con más solape de título; sin solape, el mejor retenido."""
    own_id = video.get("video_id")
    own_words = _words(video.get("title"))
    best, best_key = None, (0, "")
    for c in candidates:
        if c.get("video_id") == own_id:
            continue
        score = len(own_words & _words(c.get("title")))
        key = (score, c.get("date") or "")
        if score >= 1 and key > best_key:
            best, best_key = c, key
    if best:
        return best
    if retention_best and retention_best.get("video_id") != own_id:
        return retention_best
    return None


def _retention_best():
    """El vídeo con mejor retención del histórico (vistas >= 5)."""
    best = None
    try:
        with open(HISTORY_PATH, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if (row.get("vistas") or 0) < 5 or row.get("retencion_pct") is None:
                    continue
                if best is None or row["retencion_pct"] > best["retencion_pct"]:
                    best = row
    except Exception:
        return None
    if best:
        return {"video_id": best["video_id"], "title": best.get("titulo") or ""}
    return None


def _load_done():
    try:
        with open(DONE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_done(done):
    with open(DONE_PATH, "w", encoding="utf-8") as fh:
        json.dump(done, fh, ensure_ascii=False, indent=2)


def _trigger_text(browser):
    trigger = browser.find_element(By.ID, "linked-video-editor-link")
    return trigger, trigger.find_element(
        By.CSS_SELECTOR, ".dropdown-trigger-text"
    ).text.strip()


def set_related(browser, video_id, target):
    """Fija el vídeo relacionado en la página edit. Devuelve True si lo puso."""
    browser.get(f"https://studio.youtube.com/video/{video_id}/edit")
    time.sleep(8)
    trigger, current = _trigger_text(browser)
    if current and current != "Ninguno":
        return "ya_tenia"

    browser.execute_script(
        "arguments[0].scrollIntoView(); arguments[0].click();", trigger
    )
    time.sleep(5)

    # Buscar el vídeo objetivo por título y clicar su tarjeta (la miniatura
    # lleva el ID: .../vi/<VIDEO_ID>/...)
    search = browser.find_element(By.ID, "search-yours")
    search.clear()
    search.send_keys(target["title"][:40])
    time.sleep(4)

    card = None
    for c in browser.find_elements(By.TAG_NAME, "ytcp-entity-card"):
        if not c.is_displayed():
            continue
        html = c.get_attribute("outerHTML") or ""
        if f"/vi/{target['video_id']}/" in html:
            card = c
            break
    if card is None:
        raise RuntimeError(f"tarjeta de {target['video_id']} no encontrada")
    browser.execute_script("arguments[0].click();", card)
    time.sleep(4)

    # Guardar la página y confirmar que el trigger ya no dice "Ninguno"
    save = browser.find_element(By.ID, "save")
    browser.execute_script("arguments[0].click();", save)
    time.sleep(6)
    _, after = _trigger_text(browser)
    if after == "Ninguno":
        raise RuntimeError("el campo sigue en 'Ninguno' tras guardar")
    return "puesto"


def main():
    parser = argparse.ArgumentParser(description="Rellena vídeos relacionados")
    parser.add_argument("--max", type=int, default=8)
    args = parser.parse_args()

    account = get_accounts("youtube")[0]
    videos = []
    for v in account.get("videos", []):
        vid = _video_id(v)
        if not vid:
            continue
        videos.append(dict(v, video_id=vid))

    done = _load_done()
    now = datetime.now()
    pending = []
    for v in videos:
        if v["video_id"] in done:
            continue
        try:
            age = (now - datetime.strptime(v["date"], "%Y-%m-%d %H:%M:%S")).days
        except Exception:
            continue
        if age <= MAX_AGE_DAYS:
            pending.append(v)
    pending.sort(key=lambda v: v["date"], reverse=True)
    pending = pending[: args.max]
    if not pending:
        print("[related] Nada pendiente.")
        return

    retention_best = _retention_best()
    print(f"[related] {len(pending)} vídeos pendientes...")
    browser = open_studio_browser()
    puestos = 0
    try:
        for v in pending:
            target = choose_related(v, videos, retention_best)
            if target is None:
                print(f"[related] {v['video_id']}: sin candidato, lo salto.")
                continue
            try:
                result = set_related(browser, v["video_id"], target)
            except Exception as e:
                print(f"[related] {v['video_id']}: fallo ({e}); sigo.")
                continue
            done[v["video_id"]] = {
                "related": target["video_id"] if result == "puesto" else None,
                "estado": result,
                "fecha": now.strftime("%Y-%m-%d"),
            }
            _save_done(done)
            if result == "puesto":
                puestos += 1
                print(
                    f"[related] {v['video_id']} -> {target['video_id']} "
                    f"({target['title'][:50]})"
                )
            else:
                print(f"[related] {v['video_id']}: ya tenía relacionado.")
    finally:
        browser.quit()
    print(f"[related] {puestos} relacionados puestos.")


if __name__ == "__main__":
    main()
