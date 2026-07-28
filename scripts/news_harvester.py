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

SEEN_PATH = os.path.join(ROOT, ".mp", "news_seen.json")
TOPICS_PATH = os.path.join(ROOT, "topics.txt")
MAX_AGE_HOURS = 36
MAX_CANDIDATES = 40


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
    candidates = []
    now = time.time()
    for url in get_news_feeds():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[news] Feed KO {url}: {e}")
            continue
        source = strip_html(feed.feed.get("title", url))[:40] or url
        fresh = 0
        for entry in feed.entries[:25]:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published and (now - time.mktime(published)) > MAX_AGE_HOURS * 3600:
                continue
            candidates.append(
                {
                    "title": strip_html(entry.get("title", ""))[:200],
                    "summary": strip_html(entry.get("summary", ""))[:500],
                    "link": link,
                    "source": source,
                }
            )
            fresh += 1
        print(f"[news] {source}: {fresh} noticias frescas")
    return candidates[:MAX_CANDIDATES]


def pick_stories(candidates, count):
    listing = "\n".join(
        f"{i}. [{c['source']}] {c['title']} — {c['summary'][:300]}"
        for i, c in enumerate(candidates)
    )
    response = generate_text(
        "Eres el redactor jefe de un canal español de YouTube Shorts sobre "
        "curiosidades y actualidad de videojuegos. De esta lista de noticias "
        f"de hoy, elige las {count} con más potencial para un Short de 30-40 "
        "segundos: sorprendentes, concretas y explicables sin imágenes del "
        "juego. Descarta rumores sin sustancia, notas de prensa corporativas "
        "y finanzas. Devuelve SOLO un array JSON: "
        '[{"index": <número de la lista>, "tema": "<frase gancho EN ESPAÑOL '
        'para el vídeo>", "hechos": "<los hechos clave de la noticia '
        "redactados EN ESPAÑOL, 2-4 frases con los datos concretos (fechas, "
        'cifras, nombres); traduce si la noticia está en otro idioma>"}]. '
        "Nada más.\n\n" + listing,
        temperature=0.7,
    )
    match = re.search(r"\[.*\]", response, re.DOTALL)
    picks = json.loads(match.group(0)) if match else []
    valid = []
    for pick in picks[:count]:
        idx = pick.get("index")
        if isinstance(idx, int) and 0 <= idx < len(candidates) and pick.get("tema"):
            candidate = candidates[idx]
            # Facts always in Spanish; fall back to the raw feed text for
            # Spanish-language sources if the model omitted them
            hechos = (pick.get("hechos") or "").strip() or (
                f"{candidate['title']}. {candidate['summary']}"
            )
            valid.append((candidate, pick["tema"].strip(), hechos))
    return valid


def prepend_topics(lines):
    existing = []
    if os.path.exists(TOPICS_PATH):
        with open(TOPICS_PATH, "r", encoding="utf-8-sig") as fh:
            existing = fh.readlines()

    # Keep the leading comment block on top, news right after it
    head = []
    rest = list(existing)
    while rest and (rest[0].strip().startswith("#") or not rest[0].strip()):
        head.append(rest.pop(0))

    with open(TOPICS_PATH, "w", encoding="utf-8") as fh:
        fh.writelines(head)
        for line in lines:
            fh.write(line + "\n")
        fh.writelines(rest)


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

    select_model(get_ollama_model())
    seen = load_seen()

    candidates = collect_candidates(seen)
    print(f"[news] Candidatas totales: {len(candidates)}")
    if not candidates:
        print("[news] Nada nuevo que cosechar.")
        return

    picked = pick_stories(candidates, args.count or get_news_per_day())
    if not picked:
        print("[news] El redactor jefe no eligió ninguna.")
        return

    lines = []
    for candidate, tema, hechos in picked:
        lines.append(
            f"{tema} || CONTEXTO: {hechos} (Fuente: {candidate['source']})"
        )
        seen.append(candidate["link"])
        print(f"[news] Encolada: {tema}")

    prepend_topics(lines)
    with open(SEEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(seen[-500:], fh, ensure_ascii=False, indent=1)
    print(f"[news] {len(lines)} noticias añadidas al principio de topics.txt")


if __name__ == "__main__":
    main()
