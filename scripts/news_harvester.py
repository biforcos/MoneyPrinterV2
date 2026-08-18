"""Harvests gaming news from RSS feeds into the topics queue.

Pulls the configured feeds, has the LLM pick the most Short-worthy
recent stories, and PREPENDS them to topics.txt as context-grounded
topics (tema || CONTEXTO: hechos) so the script generator sticks to
real facts. Already-used article URLs are remembered in
.mp/news_seen.json.

Usage, from the project root:
    python scripts/news_harvester.py
"""

import html
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import feedparser

from config import get_news_feeds, get_news_per_day, get_ollama_model
from llm_provider import select_model, generate_text
from watchdog import Watchdog

SEEN_PATH = os.path.join(ROOT, ".mp", "news_seen.json")
QUEUE_PATH = os.path.join(ROOT, "news_queue.json")
RECENT_PATH = os.path.join(ROOT, ".mp", "news_recent.json")
MAX_AGE_HOURS = 36
MAX_CANDIDATES = 60
MIN_NOTA = 7
# Same story often arrives through several feeds with different URLs (and
# sometimes in another language), so the seen-URL list alone cannot catch
# cross-run duplicates. Two temas sharing most of their significant words
# are the same story; at least 3 shared words guards against two distinct
# news items about the same game colliding on its name alone.
DUP_OVERLAP = 0.5
DUP_MIN_SHARED = 3
# Many shared significant words is a strong signal on its own, even when
# a verbose tema dilutes the overlap ratio (typical across languages)
DUP_SHARED_STRONG = 5
_STOPWORDS = frozenset(
    "de la el en y a los las un una unas unos del por para con se su sus al lo "
    "es son no que como mas más sobre tras este esta estos estas ya hay entre "
    "the of and to in on for with an at is are from by its new his her this "
    "that will has have было".split()
)


def load_recent():
    try:
        with open(RECENT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def _significant_words(text):
    import unicodedata

    folded = "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if not unicodedata.combining(c)
    )
    words = set(re.findall(r"[a-z0-9]+", folded))
    # "27th"/"30º" style ordinals must also match their bare number
    words |= {m for w in words for m in re.findall(r"^(\d+)[a-z]+$", w)}
    return {w for w in words if w not in _STOPWORDS and (len(w) > 1 or w.isdigit())}


def is_duplicate(tema, recent):
    words = _significant_words(tema)
    if not words:
        return False
    for prev in recent:
        prev_words = _significant_words(prev)
        if not prev_words:
            continue
        shared = words & prev_words
        if len(shared) >= DUP_SHARED_STRONG:
            return True
        if (
            len(shared) >= DUP_MIN_SHARED
            and len(shared) / min(len(words), len(prev_words)) >= DUP_OVERLAP
        ):
            return True
    return False


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html.unescape(text).split())


def load_seen():
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def collect_candidates(seen):
    per_feed = []
    now = time.time()
    for url in get_news_feeds():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[news] Feed KO {url}: {e}")
            continue
        source = strip_html(feed.feed.get("title", url))[:40] or url
        feed_candidates = []
        for entry in feed.entries[:25]:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published and (now - time.mktime(published)) > MAX_AGE_HOURS * 3600:
                continue
            feed_candidates.append(
                {
                    "title": strip_html(entry.get("title", ""))[:200],
                    "summary": strip_html(entry.get("summary", ""))[:500],
                    "link": link,
                    "source": source,
                }
            )
        print(f"[news] {source}: {len(feed_candidates)} noticias frescas")
        per_feed.append(feed_candidates)

    # Round-robin interleave: every source gets a seat at the editor's
    # table, instead of the first feeds monopolizing the candidate cap
    candidates = []
    for i in range(max((len(f) for f in per_feed), default=0)):
        for feed_candidates in per_feed:
            if i < len(feed_candidates):
                candidates.append(feed_candidates[i])
    return candidates[:MAX_CANDIDATES]


def pick_stories(candidates, count, recent):
    listing = "\n".join(
        f"{i}. [{c['source']}] {c['title']} — {c['summary'][:300]}"
        for i, c in enumerate(candidates)
    )
    recent_block = ""
    if recent:
        recent_block = (
            "\n\nYa se han publicado Shorts sobre estos temas recientes; "
            "descarta cualquier noticia que repita alguno de ellos:\n- "
            + "\n- ".join(recent[-20:])
        )
    response = generate_text(
        "Eres el redactor jefe de un canal español de YouTube Shorts sobre "
        "curiosidades y actualidad de videojuegos. De esta lista de noticias "
        f"de hoy, elige COMO MÁXIMO {count} — solo las que darían un Short "
        "de 30-40 segundos realmente potente: sorprendentes, concretas y "
        "explicables sin imágenes del juego. Sé exigente: si ninguna lo "
        "merece, devuelve un array vacío []. Descarta rumores sin sustancia, "
        "notas de prensa corporativas, finanzas y noticias confusas o "
        "ambiguas." + recent_block + "\n\nDevuelve SOLO un array JSON: "
        '[{"index": <número de la lista>, "nota": <interés para el público '
        'de 1 a 10>, "tema": "<frase gancho EN ESPAÑOL para el vídeo>", '
        '"hechos": "<los hechos clave EN ESPAÑOL, 2-4 frases con los datos '
        "concretos (fechas, cifras, nombres) SACADOS EXCLUSIVAMENTE del "
        "título y resumen de la lista — no añadas nada que no aparezca ahí; "
        'traduce si la noticia está en otro idioma>"}]. '
        "Nada más.\n\n" + listing,
        temperature=0.7,
    )
    match = re.search(r"\[.*\]", response, re.DOTALL)
    picks = json.loads(match.group(0)) if match else []
    valid = []
    chosen_temas = []
    for pick in picks[:count]:
        idx = pick.get("index")
        if not (isinstance(idx, int) and 0 <= idx < len(candidates) and pick.get("tema")):
            continue
        tema = pick["tema"].strip()
        nota = pick.get("nota")
        if isinstance(nota, (int, float)) and nota < MIN_NOTA:
            print(f"[news] Descartada por floja (nota {nota}): {tema}")
            continue
        if is_duplicate(tema, recent + chosen_temas):
            print(f"[news] Descartada por repetida: {tema}")
            continue
        candidate = candidates[idx]
        # Facts always in Spanish; fall back to the raw feed text for
        # Spanish-language sources if the model omitted them
        hechos = (pick.get("hechos") or "").strip() or (
            f"{candidate['title']}. {candidate['summary']}"
        )
        chosen_temas.append(tema)
        valid.append((candidate, tema, hechos))
    return valid


def queue_news(items):
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as fh:
            queue = json.load(fh)
    except Exception:
        queue = []
    queue.extend(items)
    with open(QUEUE_PATH, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, ensure_ascii=False, indent=1)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Cosecha noticias gaming al topics.txt")
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="cuántas noticias encolar (0 = usar news_per_day de la config)",
    )
    args = parser.parse_args()

    # La cosecha + redactor jefe tarda minutos; 30 min = cuelgue seguro
    # (el 18-08 estuvo 10h bloqueado en una llamada LLM).
    watchdog = Watchdog(minutes=30, label="news_harvester", kill_comfyui=False)

    select_model(get_ollama_model())
    seen = load_seen()
    recent = load_recent()

    candidates = collect_candidates(seen)
    print(f"[news] Candidatas totales: {len(candidates)}")
    if not candidates:
        print("[news] Nada nuevo que cosechar.")
        watchdog.disarm()
        return

    picked = pick_stories(candidates, args.count or get_news_per_day(), recent)
    if not picked:
        print("[news] El redactor jefe no eligió ninguna.")
        watchdog.disarm()
        return

    from datetime import datetime

    stamp = datetime.now().isoformat(timespec="minutes")
    items = []
    for candidate, tema, hechos in picked:
        items.append(
            {
                "tema": tema,
                "contexto": f"{hechos} (Fuente: {candidate['source']})",
                "fuente": candidate["source"],
                "fecha": stamp,
            }
        )
        seen.append(candidate["link"])
        print(f"[news] Encolada: {tema}")

    queue_news(items)
    with open(SEEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(seen[-500:], fh, ensure_ascii=False, indent=1)
    recent.extend(item["tema"] for item in items)
    with open(RECENT_PATH, "w", encoding="utf-8") as fh:
        json.dump(recent[-40:], fh, ensure_ascii=False, indent=1)
    print(f"[news] {len(items)} noticias añadidas a news_queue.json")
    watchdog.disarm()


if __name__ == "__main__":
    main()
