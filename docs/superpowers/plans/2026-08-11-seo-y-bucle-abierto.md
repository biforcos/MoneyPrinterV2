# SEO de términos de búsqueda + guion en bucle abierto — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recolectar los términos de búsqueda reales por vídeo cada noche y usarlos en metadatos e informe semanal; y reservar la revelación del guion para la última frase.

**Architecture:** Parser puro + scraper en `channel_report.py` (patrón idéntico a retención), acumulador `.mp/search_terms.json` en `daily_analytics.py` dentro de la misma sesión de navegador, lector con filtro de solapamiento en `YouTube.generate_metadata`, y un bloque nuevo en el prompt de `generate_script`.

**Tech Stack:** Python 3.12, Selenium/Firefox headless, tests fríos en scratchpad contra volcados reales.

## Global Constraints

- Ficheros UTF-8 con literales en español; `py_compile` antes de cada commit.
- Fallos de scraping por vídeo nunca bloquean el bucle nocturno.
- Los términos se guardan tal cual (lower/strip) — los typos son el dato.
- No ejecutar scrapers reales entre las 19:55 y 20:10 (News 2000 usa el perfil).

---

### Task 1: Parser + scraper de términos en `channel_report.py`

**Files:**
- Modify: `scripts/channel_report.py` (junto a `_parse_retention_metrics`)
- Test: `<scratchpad>/test_search_terms_parse.py` (fixture: `analytics_dump_tab-reach_viewers.txt`)

**Interfaces:**
- Produces:
  - `_parse_search_terms(page_text) -> dict` — `{"busqueda_pct": float|None, "terminos": list[tuple[str, float]]}`.
  - `scrape_video_search(browser, video_id) -> dict` — mismo dict, navegando a `tab-reach_viewers`.

- [ ] **Step 1: Test frío (falla por import)** — contra el volcado real: `busqueda_pct == 71.9` y `terminos` con 5 pares terminando en `("videos de ataque a los titanes", 4.4)`; sintético con "No hay suficientes datos" → lista vacía; texto sin secciones → `{"busqueda_pct": None, "terminos": []}`.

- [ ] **Step 2: Implementar**

```python
SEARCH_SECTION_LABEL = "términos de búsqueda de youtube"
SEARCH_SOURCE_LABEL = "búsqueda de youtube"
_PCT_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*%$")


def _parse_search_terms(page_text):
    """Fuentes de tráfico + términos de búsqueda de la pestaña Cobertura."""
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    out = {"busqueda_pct": None, "terminos": []}
    for i, line in enumerate(lines):
        low = line.lower()
        if out["busqueda_pct"] is None and low == SEARCH_SOURCE_LABEL:
            if i + 1 < len(lines):
                match = _PCT_RE.match(lines[i + 1])
                if match:
                    out["busqueda_pct"] = float(match.group(1).replace(",", "."))
        if low == SEARCH_SECTION_LABEL and not out["terminos"]:
            j = i + 1
            # Cabeceras: "Visualizaciones · ...", "Proporción de todo tu
            # tráfico:" y su porcentaje
            while j < len(lines) and (
                lines[j].lower().startswith(("visualizaciones", "proporción"))
                or _PCT_RE.match(lines[j])
            ):
                j += 1
            while j + 1 < len(lines) and lines[j] != "Ver más":
                match = _PCT_RE.match(lines[j + 1])
                if not match:
                    break
                out["terminos"].append(
                    (lines[j].lower(), float(match.group(1).replace(",", ".")))
                )
                j += 2
    return out


def scrape_video_search(browser, video_id):
    browser.get(
        f"https://studio.youtube.com/video/{video_id}/analytics/tab-reach_viewers/period-default"
    )
    result = {"busqueda_pct": None, "terminos": []}
    for _ in range(6):
        time.sleep(4)
        page_text = browser.find_element(By.TAG_NAME, "body").text
        result = _parse_search_terms(page_text)
        if result["terminos"]:
            break
    return result
```

- [ ] **Step 3: Test pasa + py_compile + commit** (`git commit -m "Scraper de terminos de busqueda por video"`)

---

### Task 2: Acumulador nocturno en `daily_analytics.py`

**Files:**
- Modify: `scripts/daily_analytics.py`

**Interfaces:**
- Consumes: `scrape_video_search` (Task 1; añadir al import de `channel_report`).
- Produces: `.mp/search_terms.json` (`SEARCH_TERMS_PATH`), `busqueda_pct` en registros cruzados y en el histórico.

- [ ] **Step 1: Implementar**

Constante: `SEARCH_TERMS_PATH = os.path.join(ROOT, ".mp", "search_terms.json")`.

Función de merge:

```python
def merge_search_terms(crossed):
    """Acumula los términos de búsqueda de la noche en search_terms.json."""
    try:
        with open(SEARCH_TERMS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {"terminos": {}}
    hoy = datetime.now().strftime("%Y-%m-%d")
    nuevos = 0
    for video in crossed:
        for term, pct in video.get("search_terms") or []:
            entry = data["terminos"].setdefault(
                term, {"apariciones": 0, "ultimo": hoy, "pct_max": 0.0}
            )
            if entry["apariciones"] == 0:
                nuevos += 1
            entry["apariciones"] += 1
            entry["ultimo"] = hoy
            entry["pct_max"] = max(entry["pct_max"], pct)
    if not nuevos and not any(v.get("search_terms") for v in crossed):
        return
    data["updated"] = datetime.now().isoformat(timespec="minutes")
    with open(SEARCH_TERMS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(
        f"[analytics] Términos de búsqueda: {nuevos} nuevos, "
        f"{len(data['terminos'])} acumulados"
    )
```

En el bucle de candidatos de `main()`, tras el scrape de retención de cada vídeo:

```python
            try:
                search = scrape_video_search(browser, vid)
            except Exception as e:
                print(f"[analytics] Búsqueda fallida para {vid}: {e}")
                search = {"busqueda_pct": None, "terminos": []}
            video["busqueda_pct"] = search["busqueda_pct"]
            video["search_terms"] = search["terminos"]
```

Tras el bucle (fuera del `finally`): `merge_search_terms(crossed)`. Y en `append_retention_history`, añadir `"busqueda_pct": v.get("busqueda_pct")` a la línea.

- [ ] **Step 2: py_compile + test frío de merge** (sintético: dos vídeos con términos solapados → apariciones acumuladas) + commit

---

### Task 3: Consumo en `generate_metadata` (`src/classes/YouTube.py`)

**Files:**
- Modify: `src/classes/YouTube.py` (`generate_metadata` y helper nuevo)
- Test: `<scratchpad>/test_matching_terms.py`

**Interfaces:**
- Produces: `YouTube._matching_search_terms(subject, max_terms=5) -> list[str]` (método estático).

- [ ] **Step 1: Helper**

```python
    @staticmethod
    def _matching_search_terms(subject, max_terms=5):
        """Términos de búsqueda reales que comparten palabras con el tema."""
        path = os.path.join(ROOT_DIR, ".mp", "search_terms.json")
        try:
            with open(path, encoding="utf-8") as fh:
                terms = json.load(fh).get("terminos", {})
        except Exception:
            return []

        def words(text):
            text = text.lower().translate(str.maketrans("áéíóúü", "aeiouu"))
            return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 4}

        subject_words = words(subject)
        scored = [
            (data.get("apariciones", 0), term)
            for term, data in terms.items()
            if words(term) & subject_words
        ]
        return [term for _, term in sorted(scored, reverse=True)[:max_terms]]
```

- [ ] **Step 2: Enchufar a los prompts** — al principio de `generate_metadata`:

```python
        search_terms = self._matching_search_terms(self.subject)
        search_hint = (
            "REAL search queries people typed to find similar videos "
            "(misspellings are intentional, keep them if you use them): "
            + "; ".join(search_terms)
            + ". If one fits naturally, work its wording into the text. "
        ) if search_terms else ""
```

En el prompt del título, insertar `{search_hint}` justo antes de "Return ONE single title". En el de la descripción, antes de "Do not use markdown".

- [ ] **Step 3: Test frío del helper** (fixture sintético de search_terms.json apuntando ROOT_DIR al scratchpad no es viable — el path es fijo; testear `words`/solapamiento indirectamente creando `.mp/search_terms.json` temporal NO: usar el real si existe y aceptar lista vacía; el caso con datos se valida en E2E) — el test comprueba que con fichero ausente devuelve `[]` sin excepción y que `words("Atack on Titan 3")` ∩ `words("¿Attack on Titan 3 o sus anteriores entregas?")` no es vacío vía llamada real tras poblar el JSON en E2E. py_compile + commit.

---

### Task 4: Bucle abierto en el prompt de `generate_script`

**Files:**
- Modify: `src/classes/YouTube.py` (literal del prompt, tras el bloque FIRST SENTENCE ~línea 582)

- [ ] **Step 1: Añadir el bloque** (entre el párrafo de FIRST SENTENCE y el de LAST SENTENCE):

```
        HOLD THE REVEAL: the first sentence opens a question or mystery the
        viewer needs answered. The middle sentences add context, stakes and
        escalation WITHOUT resolving it. Only the LAST sentence delivers the
        actual answer or payoff — if the viewer leaves early, they never get
        it. EXAMPLE 2 above follows exactly this shape.
```

- [ ] **Step 2: py_compile + commit**

---

### Task 5: Validación E2E diferida (nightly 12-ago)

- [ ] `search_terms.json` poblado, `busqueda_pct` en `analytics_daily.json` e histórico, línea `[analytics] Términos de búsqueda:` en el log.
- [ ] Primer guion nuevo: la revelación está en la última frase.
- [ ] Primer título generado con un término coincidente disponible: comprobar si lo incorpora con naturalidad.
