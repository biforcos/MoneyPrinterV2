# A/B de duración de guion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A/B por vídeo de guion corto (3-4 frases) vs normal (5-7), persistido en youtube.json junto a la duración real del MP4, con readout en el bucle diario.

**Architecture:** Nueva clave de config con getter (patrón `loop_ending_ratio`), moneda al aire en `generate_script`, captura de `final_clip.duration` en `combine`, dos campos nuevos en `add_video`, y el campo añadido a los readouts genéricos de `daily_analytics.py`.

**Tech Stack:** Python 3.12; sin framework de tests (verificación con py_compile + test frío en scratchpad).

## Global Constraints

- Ficheros UTF-8 con literales en español; `config.json` local no se commitea, `config.example.json` sí.
- `py_compile` de cada fichero tocado antes de commit.
- Grupos disjuntos: corto = `randint(3, 4)`, normal = rango de config `[5, 7]`.

---

### Task 1: Config y getter

**Files:**
- Modify: `src/config.py` (tras `get_loop_ending_ratio`, ~línea 268)
- Modify: `config.json` (`short_script_ratio: 0.5`, `script_sentence_length_range: [5, 7]`)
- Modify: `config.example.json` (añadir `short_script_ratio: 0.5`)

**Interfaces:**
- Produces: `get_short_script_ratio() -> float` — probabilidad 0.0-1.0 de guion corto; default 0.0 si la clave falta.

- [ ] **Step 1: Getter en `src/config.py`**

```python
def get_short_script_ratio() -> float:
    """
    Fraction of videos (0.0-1.0) that get a short script (3-4 sentences)
    for the duration A/B; the rest use the configured sentence range.

    Returns:
        ratio (float): Probability of a short script per video
    """
    with open(os.path.join(ROOT_DIR, "config.json"), "r", encoding="utf-8") as file:
        return float(json.load(file).get("short_script_ratio", 0.0))
```

- [ ] **Step 2: `config.json`** — `"short_script_ratio": 0.5` junto a `loop_ending_ratio`, y `script_sentence_length_range` a `[5, 7]`.

- [ ] **Step 3: `config.example.json`** — añadir `"short_script_ratio": 0.5` en la misma zona.

- [ ] **Step 4: Verificar en frío**

Run: `venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from config import get_short_script_ratio; print(get_short_script_ratio())"`
Expected: `0.5`

- [ ] **Step 5: Commit** (`src/config.py` + `config.example.json`; config.json es local)

---

### Task 2: Moneda al aire en `generate_script` + captura de duración + `add_video`

**Files:**
- Modify: `src/classes/YouTube.py:518-522` (selección de longitud), `~2100` (antes de `write_videofile`), `2635-2646` (dict de `add_video`)

**Interfaces:**
- Consumes: `get_short_script_ratio()` (Task 1; añadir al import de `config`).
- Produces: registro de vídeo con `short_script` (bool) y `dur_video_s` (float|None).

- [ ] **Step 1: Selección de longitud** — sustituir las líneas 518-522 por:

```python
        # Duration A/B: short scripts (3-4 sentences) vs the configured
        # range, decided per video and persisted for analytics
        self._short_script = random.random() < get_short_script_ratio()
        if self._short_script:
            sentence_length = random.randint(3, 4)
        else:
            length_range = get_script_sentence_length_range()
            if len(length_range) == 2:
                sentence_length = random.randint(length_range[0], length_range[1])
            else:
                sentence_length = get_script_sentence_length()
```

- [ ] **Step 2: Duración real en `combine`** — justo antes de `raw_path = combined_image_path + ".raw.mp4"`:

```python
        self._video_duration = round(final_clip.duration, 1)
```

- [ ] **Step 3: Campos en `add_video`** — en el dict de la llamada (tras `"news"`):

```python
                    "short_script": bool(getattr(self, "_short_script", False)),
                    "dur_video_s": getattr(self, "_video_duration", None),
```

- [ ] **Step 4: Compilar y commit**

Run: `venv\Scripts\python.exe -m py_compile src\classes\YouTube.py`

---

### Task 3: Readouts en `daily_analytics.py`

**Files:**
- Modify: `scripts/daily_analytics.py` (dict `readouts` y tupla `retention_fields` en `main`)

**Interfaces:**
- Consumes: campo `short_script` en los registros de youtube.json (Task 2).

- [ ] **Step 1: Añadir el campo a ambos readouts**

```python
    readouts = {
        "loop_ending": ab_readout(crossed, "loop_ending"),
        "news": ab_readout(crossed, "news"),
        "short_script": ab_readout(crossed, "short_script"),
    }
```

```python
    retention_fields = ("loop_ending", "news", "mood", "dialogue", "short_script")
```

- [ ] **Step 2: Compilar, verificar y commit**

Run: `venv\Scripts\python.exe -m py_compile scripts\daily_analytics.py` y re-ejecutar el test frío de scratchpad `test_retention_readout.py`.

---

### Task 4: Validación E2E diferida

- [ ] El nightly de las 3:00 genera con el A/B activo. Mañana: verificar que los vídeos nuevos en `.mp/youtube.json` traen `short_script` y `dur_video_s`, y que el readout `[analytics] A/B short_script` aparece en `logs/nightly.log` (n crecerá con los días).
