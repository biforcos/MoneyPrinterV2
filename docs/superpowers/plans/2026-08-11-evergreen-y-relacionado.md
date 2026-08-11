# Evergreen + vídeo relacionado — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encolar 1 tema evergreen/noche desde la demanda de búsqueda, y rellenar el campo "Vídeo relacionado" de los Shorts recientes con un barrido nocturno.

**Architecture:** Parte A: función pura de candidatos + conversión LLM en `daily_analytics.py`, apéndice a `topics.txt`. Parte B: script nuevo `related_videos.py` (Selenium, perfil bot, estado en `.mp/related_done.json`) añadido al nightly.

**Tech Stack:** Python 3.12, difflib (stdlib), Selenium/Firefox headless, tests fríos en scratchpad.

## Global Constraints

- UTF-8, `py_compile` antes de cada commit, fallos por vídeo nunca rompen el nightly.
- No usar el perfil de Firefox mientras corre un ciclo de noticias o batch (verificar en el log que terminó).

---

### Task 1: Candidatos evergreen + encolado (`daily_analytics.py`)

**Files:**
- Modify: `scripts/daily_analytics.py`
- Test: `<scratchpad>/test_evergreen.py`

**Interfaces:**
- Produces:
  - `_evergreen_candidates(terms, recent_titles, today) -> list[str]` — términos elegibles ordenados por apariciones desc. `terms`: dict de search_terms.json; `recent_titles`: títulos de vídeos ≤21 días; `today`: date.
  - `propose_evergreen(cached_videos)` — elige 1, lo convierte en tema vía LLM, lo añade a `topics.txt`, marca `propuesto`.
- Constantes: `EVERGREEN_MIN_APARICIONES = 3`, `EVERGREEN_COOLDOWN_DAYS = 30`, `EVERGREEN_TITLE_WINDOW_DAYS = 21`, `TOPICS_PATH = os.path.join(ROOT, "topics.txt")`.

- [ ] **Step 1: Test frío (falla por import)**

```python
"""Test frio de _evergreen_candidates."""
import os, sys
from datetime import date
ROOT = r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2"
sys.path.insert(0, os.path.join(ROOT, "scripts")); sys.path.insert(0, os.path.join(ROOT, "src"))
from daily_analytics import _evergreen_candidates

TODAY = date(2026, 8, 11)
terms = {
    "atack on titan 3": {"apariciones": 5},                       # cubierto por titulo (typo tolerado)
    "gta 6 netflix": {"apariciones": 4},                          # elegible
    "titan acorazado": {"apariciones": 3},                        # cubierto ("titan" ~ "titan")
    "silksong fecha": {"apariciones": 2},                         # bajo umbral
    "zelda switch 2": {"apariciones": 9, "propuesto": "2026-08-01"},  # cooldown (10 dias)
    "mario kart 9": {"apariciones": 3, "propuesto": "2026-07-01"},    # cooldown vencido: elegible
}
recent = ["¿Attack on Titan 3 o sus anteriores entregas? ¡Análisis detallado!"]
cands = _evergreen_candidates(terms, recent, TODAY)
assert cands == ["gta 6 netflix", "mario kart 9"], cands
print("test_evergreen OK")
```

- [ ] **Step 2: Implementar** — en `daily_analytics.py` (imports: `import difflib`, `from datetime import date`):

```python
TOPICS_PATH = os.path.join(ROOT, "topics.txt")
EVERGREEN_MIN_APARICIONES = 3
EVERGREEN_COOLDOWN_DAYS = 30
EVERGREEN_TITLE_WINDOW_DAYS = 21


def _significant_words(text):
    text = (text or "").lower().translate(str.maketrans("áéíóúü", "aeiouu"))
    return [w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 4]


def _evergreen_candidates(terms, recent_titles, today):
    """Términos con demanda, sin cubrir hace poco y fuera de cooldown."""
    title_words = set()
    for title in recent_titles:
        title_words.update(_significant_words(title))
    out = []
    for term, data in terms.items():
        if data.get("apariciones", 0) < EVERGREEN_MIN_APARICIONES:
            continue
        propuesto = data.get("propuesto")
        if propuesto:
            try:
                age = (today - date.fromisoformat(propuesto)).days
                if age < EVERGREEN_COOLDOWN_DAYS:
                    continue
            except Exception:
                pass
        words = _significant_words(term)
        covered = words and all(
            difflib.get_close_matches(w, title_words, n=1, cutoff=0.8)
            for w in words
        )
        if covered:
            continue
        out.append((data.get("apariciones", 0), term))
    return [term for _, term in sorted(out, reverse=True)]


def propose_evergreen(cached_videos):
    """Encola en topics.txt 1 tema desde la demanda de búsqueda."""
    try:
        with open(SEARCH_TERMS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return
    cutoff = datetime.now().timestamp() - EVERGREEN_TITLE_WINDOW_DAYS * 86400
    recent_titles = []
    for v in cached_videos:
        try:
            ts = datetime.strptime(v["date"], "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            recent_titles.append(v.get("title") or "")
    cands = _evergreen_candidates(
        data.get("terminos", {}), recent_titles, datetime.now().date()
    )
    if not cands:
        return
    term = cands[0]
    tema = generate_text(
        f'La gente busca en YouTube: "{term}". Convierte esa búsqueda en un '
        "tema concreto y atractivo para un Short de videojuegos en español. "
        "Devuelve SOLO el tema, en una sola línea, sin comillas.",
        temperature=0.6,
    ).strip().splitlines()[0].strip().strip('"')
    if not tema:
        print(f"[analytics] Evergreen: el LLM no devolvió tema para '{term}'.")
        return
    with open(TOPICS_PATH, "a", encoding="utf-8") as fh:
        fh.write(tema + "\n")
    data["terminos"][term]["propuesto"] = datetime.now().strftime("%Y-%m-%d")
    with open(SEARCH_TERMS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"[analytics] Tema evergreen encolado (búsqueda '{term}'): {tema}")
```

En `main()`, tras `refresh_insights(studio_rows)`: `propose_evergreen(cached)`.

- [ ] **Step 3: Test pasa + py_compile + commit**

---

### Task 2: Descubrimiento del diálogo "Vídeo relacionado"

**Files:**
- Create: `<scratchpad>/dump_related_dialog.py` (throwaway)

- [ ] **Step 1:** Verificar en `logs/news_cycle.log` que el ciclo de las 20:00 terminó ("Apagando ComfyUI"). Con el perfil libre: abrir la página edit de un vídeo reciente, volcar `read: body.text` y el árbol de elementos del bloque "Vídeo relacionado"; clicar el lápiz y volcar el diálogo (título, campo de búsqueda, filas de vídeos, botón de guardar). Anotar selectores/textos exactos para Task 3.

---

### Task 3: `scripts/related_videos.py` + nightly

**Files:**
- Create: `scripts/related_videos.py`
- Modify: `scripts/nightly_batch.bat` (línea tras `comments_check.py`)
- Test: `<scratchpad>/test_related_choice.py` (elección del relacionado, pura)

**Interfaces:**
- Produces: `choose_related(video, candidates, retention_best) -> dict|None` (pura) y `main()` que barre y rellena. Estado `.mp/related_done.json`.

- [ ] **Step 1: Test frío de `choose_related`** — mayor solape de palabras significativas gana; empate → más reciente; sin solape → `retention_best`; nunca el propio vídeo.
- [ ] **Step 2: Implementar el script** con los selectores del descubrimiento (estructura: cargar cache ES + retention_history, candidatos pendientes ≤14 días, cap 8, sesión única `open_studio_browser`, por vídeo: edit → detectar estado → diálogo → buscar por título → seleccionar → guardar → marcar en `related_done.json`; try/except por vídeo).
- [ ] **Step 3: Ejecución manual sobre 1-2 vídeos** y verificación visual en Studio (campo relleno).
- [ ] **Step 4: Añadir al nightly** (`venv\Scripts\python.exe scripts\related_videos.py >> logs\nightly.log 2>&1`), py_compile, commit.

---

### Task 4: Validación E2E diferida (nightly 12-ago)

- [ ] Línea `[analytics] Tema evergreen encolado` (si algún término supera el umbral; con 1 día de datos puede tardar 2-3 noches).
- [ ] `related_done.json` con ~8 vídeos marcados y el campo visible en Studio.
