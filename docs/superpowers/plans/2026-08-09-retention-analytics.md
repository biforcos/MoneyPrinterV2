# Retención por vídeo en el bucle de analíticas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir % de retención y duración media por Short al bucle diario de analíticas, con A/B por retención e histórico append-only.

**Architecture:** `channel_report.py` gana `open_studio_browser()`, `scrape_shorts(browser=None)` y `scrape_video_retention(browser, video_id)` (parseo por texto de página, como el scraper existente). `daily_analytics.py` reutiliza una sesión de navegador para lista + páginas de retención, cruza métricas, imprime A/B de retención y hace append a `logs/retention_history.jsonl`. El informe semanal lee la retención del último `analytics_daily.json` sin re-scrapear.

**Tech Stack:** Python 3.12, Selenium + Firefox headless (perfil bot pre-logueado), sin framework de tests (scripts de verificación en scratchpad, patrón del proyecto).

## Global Constraints

- El proyecto se ejecuta desde la raíz; los scripts añaden `src/` y `scripts/` a `sys.path` con rutas absolutas desde `__file__`.
- Los ficheros fuente son UTF-8 con literales en español ("Público", "retención") — mantener encoding UTF-8 en todo lo escrito.
- La ventana del Firefox bot debe estar cerrada al ejecutar scrapers; no lanzar ejecuciones reales entre las 14:55 y 15:10 (tarea News 1500 usa el perfil).
- Selección de vídeos a scrapear: públicos, edad entre 24 h (`MIN_AGE_HOURS`) y 14 días, máximo 15 por noche, solo `accounts[0]` (ES).
- Un fallo de scraping por vídeo nunca aborta el bucle diario; cero métricas ⇒ se omiten A/B de retención e histórico.
- `py_compile` de cada script tocado antes de cada commit.

---

### Task 1: Volcado de descubrimiento de la página de analytics

**Files:**
- Create: `<scratchpad>/dump_analytics_page.py` (throwaway, no se commitea)
- Create: `<scratchpad>/analytics_dump.txt` (salida)

**Interfaces:**
- Produces: `analytics_dump.txt` con el texto completo de la página de analytics de un Short real — Task 2 fija las etiquetas de parseo contra este volcado.

- [ ] **Step 1: Escribir el script de volcado**

```python
"""Vuelca el texto de la pagina de analytics de un Short para fijar el parseo."""
import json
import os
import re
import sys
import time

ROOT = r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2"
sys.path.insert(0, os.path.join(ROOT, "src"))

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager

from cache import get_accounts

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics_dump.txt")

account = get_accounts("youtube")[0]
# Video reciente con algo de edad (no el de esta madrugada): usar uno del 7-8 de agosto
video = next(v for v in reversed(account["videos"]) if "2026-08-07" in v["date"] or "2026-08-08" in v["date"])
video_id = re.search(r"[?&]v=([\w-]{11})", video["url"]).group(1)
print(f"Volcando analytics de {video_id}: {video['title'][:60]}")

opts = Options()
opts.add_argument("--headless")
opts.add_argument("--width=1600")
opts.add_argument("--height=1200")
opts.add_argument("-profile")
opts.add_argument(account["firefox_profile"])
browser = webdriver.Firefox(service=Service(GeckoDriverManager().install()), options=opts)
try:
    browser.get(f"https://studio.youtube.com/video/{video_id}/analytics/tab-overview/period-default")
    time.sleep(12)
    text = browser.find_element(By.TAG_NAME, "body").text
finally:
    browser.quit()

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(text)
print(f"Guardado en {OUT} ({len(text)} chars)")
```

- [ ] **Step 2: Ejecutarlo (ventana bot cerrada) y revisar el volcado**

Run: `python <scratchpad>/dump_analytics_page.py`
Expected: fichero `analytics_dump.txt` con líneas que contengan la métrica de "siguieron viendo" (con %) y la duración media (formato `M:SS`). Anotar las etiquetas y formatos EXACTOS — Task 2 los usa. Si las etiquetas reales difieren de las previstas ("siguieron viendo", "Duración media"), usar las reales.

(Sin commit: artefactos de scratchpad.)

---

### Task 2: Parser de retención + refactor de sesión en `channel_report.py`

**Files:**
- Modify: `scripts/channel_report.py` (función `scrape_shorts`, ~línea 60)
- Test: `<scratchpad>/test_retention_parse.py`

**Interfaces:**
- Produces:
  - `open_studio_browser() -> webdriver.Firefox` — navegador headless con el perfil bot de `accounts[0]`.
  - `scrape_shorts(browser=None) -> list[dict]` — igual que antes; si `browser` es `None`, abre y cierra el suyo (compatibilidad total con `main()` del informe semanal).
  - `scrape_video_retention(browser, video_id) -> dict` — `{"retencion_pct": float|None, "dur_media_s": int|None}`.
  - `_parse_retention_metrics(page_text) -> dict` — mismo dict, parseo puro sin navegador.

- [ ] **Step 1: Escribir el test del parseo (falla: funciones no existen)**

```python
"""Test frio del parseo de retencion contra el volcado real + casos sinteticos."""
import os
import sys

ROOT = r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from channel_report import _parse_retention_metrics, _parse_duration_seconds

# 1) Volcado real
dump_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics_dump.txt")
with open(dump_path, encoding="utf-8") as fh:
    real = _parse_retention_metrics(fh.read())
assert real["retencion_pct"] is not None, f"retencion no parseada del volcado real: {real}"
assert 0 < real["retencion_pct"] <= 100, real
assert real["dur_media_s"] is not None and 0 < real["dur_media_s"] < 180, real
print(f"volcado real OK: {real}")

# 2) Sinteticos (ajustar etiquetas a las del volcado real si difieren)
synthetic = "Vistas\n42\nEspectadores que siguieron viendo\n62,1 %\nDuración media de las visualizaciones\n0:21\n"
m = _parse_retention_metrics(synthetic)
assert m == {"retencion_pct": 62.1, "dur_media_s": 21}, m

# 3) Etiquetas ausentes -> None, sin excepcion
empty = _parse_retention_metrics("Pagina sin metricas\nNada que ver\n")
assert empty == {"retencion_pct": None, "dur_media_s": None}, empty

# 4) Duraciones
assert _parse_duration_seconds("0:21") == 21
assert _parse_duration_seconds("1:05") == 65
assert _parse_duration_seconds("sin numeros") is None
print("test_retention_parse OK")
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `python <scratchpad>/test_retention_parse.py`
Expected: `ImportError: cannot import name '_parse_retention_metrics'`

- [ ] **Step 3: Implementar en `channel_report.py`**

Refactor de `scrape_shorts` — extraer la apertura del navegador y aceptar uno externo:

```python
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
        # ... cuerpo actual sin cambios (get, sleep, ytcp-video-row, parseo) ...
    finally:
        if own_browser:
            browser.quit()
    return videos
```

(El cuerpo del `try` queda idéntico al actual; solo cambian la cabecera, la apertura y el `finally`.)

Nuevas funciones de retención (etiquetas: usar las EXACTAS del volcado de Task 1; las de abajo son las previstas):

```python
RETENTION_LABELS = ("siguieron viendo",)
DURATION_LABELS = ("duración media", "duracion media")


def _parse_duration_seconds(text):
    match = re.search(r"(\d+):(\d{2})", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _parse_retention_metrics(page_text):
    """Busca las metricas en el texto de la pagina de analytics de un Short.

    Studio pinta la etiqueta y el valor en lineas contiguas, asi que se
    busca el valor en una ventana de 3 lineas desde cada etiqueta.
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


def scrape_video_retention(browser, video_id):
    browser.get(
        f"https://studio.youtube.com/video/{video_id}/analytics/tab-overview/period-default"
    )
    metrics = {"retencion_pct": None, "dur_media_s": None}
    for _ in range(6):
        time.sleep(4)
        page_text = browser.find_element(By.TAG_NAME, "body").text
        metrics = _parse_retention_metrics(page_text)
        if metrics["retencion_pct"] is not None or metrics["dur_media_s"] is not None:
            break
    return metrics
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `python <scratchpad>/test_retention_parse.py`
Expected: `volcado real OK: {...}` y `test_retention_parse OK`

- [ ] **Step 5: Compilar y commit**

Run: `python -m py_compile scripts/channel_report.py`

```bash
git add scripts/channel_report.py
git commit -m "Scraper de retencion por video en channel_report"
```

---

### Task 3: Integración en `daily_analytics.py` (A/B de retención + histórico)

**Files:**
- Modify: `scripts/daily_analytics.py`
- Test: `<scratchpad>/test_retention_readout.py`

**Interfaces:**
- Consumes: `open_studio_browser()`, `scrape_shorts(browser)`, `scrape_video_retention(browser, video_id)` de Task 2.
- Produces:
  - `retention_readout(crossed, field) -> dict` — mediana de `retencion_pct` por valor del campo (categórico o booleano): `{valor: {"n": int, "mediana_retencion_pct": float}}`.
  - `logs/retention_history.jsonl` — una línea JSON por vídeo y noche.
  - Clave `ab_retencion` en `logs/analytics_daily.json`.

- [ ] **Step 1: Escribir el test de selección y readout (falla)**

```python
"""Test frio de _retention_candidates y retention_readout con datos sinteticos."""
import os
import sys
from datetime import datetime, timedelta

ROOT = r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from daily_analytics import _retention_candidates, retention_readout, _video_id

def fake(days_ago, vid="AAAAAAAAAAA", vis="Público", **extra):
    date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return dict(
        {"title": "t", "url": f"https://www.youtube.com/watch?v={vid}", "date": date,
         "visibility": vis},
        **extra,
    )

# Seleccion: fuera <24h, fuera >14d, fuera no-publicos, fuera sin URL
videos = [
    fake(0.5, "TOOYOUNG111"),
    fake(2, "GOODVID0001"),
    fake(20, "TOOOLD00001"),
    fake(3, "SCHEDULED01", vis="Programado"),
    dict(fake(4), url=""),
]
cands = _retention_candidates(videos)
assert [vid for vid, _ in cands] == ["GOODVID0001"], cands

# Cap de 15, mas recientes primero
many = [fake(i + 1.5, f"VID{i:08d}") for i in range(20)]
cands = _retention_candidates(many)
assert len(cands) == 15, len(cands)
assert cands[0][0] == "VID00000000", cands[0]

# Readout categorico ignora videos sin retencion
crossed = [
    {"mood": "epica", "retencion_pct": 60.0},
    {"mood": "epica", "retencion_pct": 70.0},
    {"mood": "misterio", "retencion_pct": 40.0},
    {"mood": "misterio"},
]
r = retention_readout(crossed, "mood")
assert r == {
    "epica": {"n": 2, "mediana_retencion_pct": 65.0},
    "misterio": {"n": 1, "mediana_retencion_pct": 40.0},
}, r

assert _video_id({"url": "https://www.youtube.com/watch?v=nYJcHgG0W1I"}) == "nYJcHgG0W1I"
assert _video_id({"url": ""}) is None
print("test_retention_readout OK")
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `python <scratchpad>/test_retention_readout.py`
Expected: `ImportError: cannot import name '_retention_candidates'`

- [ ] **Step 3: Implementar en `daily_analytics.py`**

Import y constantes (tras `OUT_PATH`):

```python
from channel_report import (
    scrape_shorts,
    INSIGHTS_PATH,
    open_studio_browser,
    scrape_video_retention,
)

HISTORY_PATH = os.path.join(ROOT, "logs", "retention_history.jsonl")
RETENTION_MAX_VIDEOS = 15
RETENTION_MAX_AGE_DAYS = 14
```

En `cross_stats`, propagar la visibilidad de Studio (línea del `crossed.append`):

```python
crossed.append(
    dict(video, views=row.get("views"), visibility=row.get("visibility"))
)
```

Nuevas funciones:

```python
def _video_id(video):
    match = re.search(r"[?&]v=([\w-]{11})", video.get("url") or "")
    return match.group(1) if match else None


def _retention_candidates(crossed):
    """Publicos, entre 24h y 14 dias, mas recientes primero, cap de 15."""
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
    """Mediana de retencion por valor del campo (categorico o booleano)."""
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
```

Reescribir `main()` — una sola sesión de navegador, retención tras el cruce:

```python
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
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `python <scratchpad>/test_retention_readout.py`
Expected: `test_retention_readout OK`

- [ ] **Step 5: Compilar y commit**

Run: `python -m py_compile scripts/daily_analytics.py`

```bash
git add scripts/daily_analytics.py
git commit -m "A/B de retencion e historico en el bucle diario"
```

---

### Task 4: Retención en el informe semanal

**Files:**
- Modify: `scripts/channel_report.py` (función `main`, tabla y prompt del LLM)

**Interfaces:**
- Consumes: `logs/analytics_daily.json` con `videos[*].retencion_pct` / `dur_media_s` (Task 3).
- Produces: tabla del LLM semanal con sufijo de retención cuando hay datos.

- [ ] **Step 1: Implementar el mapa de retención y la tabla enriquecida**

En `channel_report.py`, tras `REPORT_PATH`:

```python
DAILY_PATH = os.path.join(ROOT, "logs", "analytics_daily.json")


def _norm_title(text):
    return " ".join(re.sub(r"[\W_]+", " ", (text or "").lower()).split())[:60]


def _load_retention_map():
    """titulo normalizado -> metricas de retencion del ultimo bucle diario."""
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
```

En `main()`, sustituir la construcción de `table` por:

```python
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
```

Y en el prompt del análisis, cambiar la línea 1) por:

```python
        "1) Qué patrones separan los vídeos con más vistas y más retención "
        "de los demás (tema, formato del título, franquicia, duración).\n"
```

- [ ] **Step 2: Verificación en frío**

Run: `python -c "import sys; sys.path.insert(0, 'scripts'); sys.path.insert(0, 'src'); from channel_report import _load_retention_map; print(_load_retention_map())"`
Expected: `{}` (el analytics_daily.json actual aún no tiene retención) — sin excepción.

- [ ] **Step 3: Compilar y commit**

Run: `python -m py_compile scripts/channel_report.py`

```bash
git add scripts/channel_report.py
git commit -m "Retencion en la tabla del informe semanal"
```

---

### Task 5: Ejecución end-to-end real y verificación

**Files:**
- Verifica: `logs/analytics_daily.json`, `logs/retention_history.jsonl`

- [ ] **Step 1: Ejecutar el bucle diario completo (ventana bot cerrada, fuera de 14:55–15:10)**

Run: `python scripts/daily_analytics.py` (timeout ≥ 8 min)
Expected: líneas `[analytics] Scrapeando retención de N vídeos...`, readouts `Retención por mood/loop_ending/...`, sin tracebacks.

- [ ] **Step 2: Verificar salidas**

- `logs/analytics_daily.json` contiene `ab_retencion` con grupos no vacíos y vídeos con `retencion_pct`.
- `logs/retention_history.jsonl` existe con una línea por vídeo scrapeado, JSON válido, `fecha` de hoy.

- [ ] **Step 3: Verificar que el informe semanal sigue funcionando en frío**

Run: `python -m py_compile scripts/channel_report.py scripts/daily_analytics.py`
Expected: sin errores. (El informe semanal completo se valida solo el día 16; el cambio de tabla se verificó en Task 4 Step 2.)

- [ ] **Step 4: Commit final si hubo ajustes de etiquetas**

```bash
git add scripts/channel_report.py scripts/daily_analytics.py
git commit -m "Ajustes de parseo tras la ejecucion real de retencion"
```

(Solo si la ejecución real obligó a retocar etiquetas o tiempos de espera.)
