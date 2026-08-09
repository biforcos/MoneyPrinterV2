"""Daily analytics loop: Studio stats -> winning themes + loop A/B.

Reuses channel_report's Studio scraper to pull per-Short views, crosses
them with .mp/youtube.json (loop_ending, news, mood, dialogue), refreshes
the winning themes in .mp/audience_insights.json (which generate_topic
already consumes) and prints a loop-ending A/B readout for the briefing.
Detailed output goes to logs/analytics_daily.json.

Runs in the nightly batch before generation. Usage, from the project
root (bot Firefox window must be closed):
    python scripts/daily_analytics.py
"""

import json
import os
import re
import statistics
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from channel_report import (
    scrape_shorts,
    INSIGHTS_PATH,
    open_studio_browser,
    scrape_video_retention,
)

from cache import get_accounts
from config import get_ollama_model
from llm_provider import select_model, generate_text

OUT_PATH = os.path.join(ROOT, "logs", "analytics_daily.json")
HISTORY_PATH = os.path.join(ROOT, "logs", "retention_history.jsonl")
# Views need time to accumulate; younger videos would poison the A/B
MIN_AGE_HOURS = 24
RETENTION_MAX_VIDEOS = 15
RETENTION_MAX_AGE_DAYS = 14


def _norm(text):
    return " ".join(re.sub(r"[\W_]+", " ", (text or "").lower()).split())


def cross_stats(studio_rows, cached_videos):
    """
    Attaches Studio view counts to the cached video records. Studio
    truncates titles, so matching is by normalized prefix.
    """
    rows = [dict(r, _norm=_norm(r.get("title"))[:60]) for r in studio_rows]
    crossed = []
    for video in cached_videos:
        key = _norm(video.get("title"))[:60]
        if not key:
            continue
        for row in rows:
            other = row["_norm"]
            if other and (key.startswith(other) or other.startswith(key)):
                crossed.append(
                    dict(video, views=row.get("views"), visibility=row.get("visibility"))
                )
                rows.remove(row)
                break
    return crossed


def _views_per_day(video):
    try:
        published = datetime.strptime(video["date"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    age_days = (datetime.now() - published).total_seconds() / 86400
    if age_days < MIN_AGE_HOURS / 24 or video.get("views") is None:
        return None
    return video["views"] / age_days


def ab_readout(crossed, field):
    """
    Median views/day for the two values of a boolean field, among videos
    old enough to have been served by the feed.
    """
    groups = {True: [], False: []}
    for video in crossed:
        if field not in video:
            continue
        vpd = _views_per_day(video)
        if vpd is not None:
            groups[bool(video[field])].append(vpd)
    return {
        str(flag): {
            "n": len(values),
            "mediana_vistas_dia": round(statistics.median(values), 1) if values else None,
        }
        for flag, values in groups.items()
    }


def _video_id(video):
    match = re.search(r"[?&]v=([\w-]{11})", video.get("url") or "")
    return match.group(1) if match else None


def _retention_candidates(crossed):
    """Públicos, entre 24 h y 14 días, más recientes primero, cap de 15."""
    out = []
    for video in crossed:
        vid = _video_id(video)
        if not vid or video.get("visibility") != "Público":
            continue
        try:
            published = datetime.strptime(video["date"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        age_days = (datetime.now() - published).total_seconds() / 86400
        if MIN_AGE_HOURS / 24 <= age_days <= RETENTION_MAX_AGE_DAYS:
            out.append((published, vid, video))
    out.sort(key=lambda item: item[0], reverse=True)
    return [(vid, video) for _, vid, video in out[:RETENTION_MAX_VIDEOS]]


def retention_readout(crossed, field):
    """Mediana de retención por valor del campo (categórico o booleano)."""
    groups = {}
    for video in crossed:
        if field not in video or video.get("retencion_pct") is None:
            continue
        groups.setdefault(str(video[field]), []).append(video["retencion_pct"])
    return {
        value: {
            "n": len(values),
            "mediana_retencion_pct": round(statistics.median(values), 1),
        }
        for value, values in sorted(groups.items())
    }


def append_retention_history(crossed):
    rows = [
        v
        for v in crossed
        if v.get("retencion_pct") is not None or v.get("dur_media_s") is not None
    ]
    if not rows:
        return
    with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
        for v in rows:
            fh.write(
                json.dumps(
                    {
                        "fecha": datetime.now().strftime("%Y-%m-%d"),
                        "video_id": v.get("video_id"),
                        "titulo": (v.get("title") or "")[:80],
                        "retencion_pct": v.get("retencion_pct"),
                        "dur_media_s": v.get("dur_media_s"),
                        "vistas": v.get("views"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def refresh_insights(studio_rows):
    public = [
        v
        for v in studio_rows
        if v.get("visibility") == "Público" and v.get("views") is not None
    ]
    if len(public) < 8:
        print("[analytics] Pocos vídeos públicos para refrescar insights.")
        return None
    ranked = sorted(public, key=lambda v: -(v.get("views") or 0))
    sample = ranked[:10] + ranked[-8:]
    table = "\n".join(
        f"- {v.get('views', 0)} vistas ({v.get('date', '?')}): {v['title']}"
        for v in sample
    )
    answer = generate_text(
        "Eres el analista de un canal de YouTube Shorts español de "
        "videojuegos. Estos son los Shorts con más y menos vistas del "
        "canal. Deduce qué temáticas funcionan y devuelve SOLO un JSON: "
        '{"temas_ganadores": ["...", "..."]} con 3-5 temáticas concretas '
        "a potenciar. Nada más.\n\n" + table,
        temperature=0.4,
    )
    match = re.search(r'\{\s*"temas_ganadores"\s*:\s*\[.*?\]\s*\}', answer, re.DOTALL)
    if not match:
        print("[analytics] El LLM no devolvió temas_ganadores.")
        return None
    try:
        insights = json.loads(match.group(0))
    except Exception as e:
        print(f"[analytics] JSON de insights inválido: {e}")
        return None
    insights["updated"] = datetime.now().isoformat(timespec="minutes")
    with open(INSIGHTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(insights, fh, ensure_ascii=False, indent=2)
    print(f"[analytics] Temas ganadores refrescados: {insights['temas_ganadores']}")
    return insights


def main():
    print(f"[analytics] Leyendo Studio... ({datetime.now():%d/%m %H:%M})")
    browser = open_studio_browser()
    try:
        studio_rows = scrape_shorts(browser)
        if not studio_rows:
            print("[analytics] Studio no devolvió vídeos; abortando sin tocar nada.")
            return

        accounts = get_accounts("youtube")
        cached = accounts[0].get("videos", []) if accounts else []
        crossed = cross_stats(studio_rows, cached)
        print(
            f"[analytics] {len(studio_rows)} filas de Studio, "
            f"{len(crossed)} cruzadas con youtube.json"
        )

        candidates = _retention_candidates(crossed)
        print(f"[analytics] Scrapeando retención de {len(candidates)} vídeos...")
        for vid, video in candidates:
            try:
                metrics = scrape_video_retention(browser, vid)
            except Exception as e:
                print(f"[analytics] Retención fallida para {vid}: {e}")
                continue
            if metrics["retencion_pct"] is None and metrics["dur_media_s"] is None:
                print(
                    f"[analytics] Sin métricas parseables para {vid} "
                    "(¿cambió la UI de Studio?)"
                )
                continue
            video.update(metrics)
            video["video_id"] = vid
    finally:
        browser.quit()

    readouts = {
        "loop_ending": ab_readout(crossed, "loop_ending"),
        "news": ab_readout(crossed, "news"),
    }
    for field, groups in readouts.items():
        yes, no = groups["True"], groups["False"]
        print(
            f"[analytics] A/B {field}: sí n={yes['n']} "
            f"mediana={yes['mediana_vistas_dia']} vistas/día | "
            f"no n={no['n']} mediana={no['mediana_vistas_dia']} vistas/día"
        )

    retention_fields = ("loop_ending", "news", "mood", "dialogue")
    ab_retencion = {f: retention_readout(crossed, f) for f in retention_fields}
    for field, groups in ab_retencion.items():
        if not groups:
            continue
        parts = " | ".join(
            f"{value} n={g['n']} mediana={g['mediana_retencion_pct']}%"
            for value, g in groups.items()
        )
        print(f"[analytics] Retención por {field}: {parts}")

    append_retention_history(crossed)

    select_model(get_ollama_model())
    refresh_insights(studio_rows)

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated": datetime.now().isoformat(timespec="minutes"),
                "ab": readouts,
                "ab_retencion": ab_retencion,
                "videos": crossed,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )
    print(f"[analytics] Detalle guardado en {OUT_PATH}")


if __name__ == "__main__":
    main()
