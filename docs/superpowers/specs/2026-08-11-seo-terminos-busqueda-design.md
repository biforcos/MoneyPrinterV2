# Bucle SEO con términos de búsqueda reales

**Fecha:** 2026-08-11
**Estado:** aprobado

## Objetivo

El 62-72 % del tráfico de varios Shorts viene de Búsqueda de YouTube, y Studio expone los términos exactos (typos incluidos: "atack on titan 3"). Cerrar el bucle: recolectarlos cada noche, acumularlos, y que la generación de metadatos y el informe semanal los exploten.

## Diseño

### 1. Scraper (`scripts/channel_report.py`)

- `_parse_search_terms(page_text) -> dict` (puro, testeable en frío contra el volcado real `analytics_dump_tab-reach_viewers.txt`):
  - `busqueda_pct` (float|None): el % junto a "Búsqueda de YouTube" en "Fuentes de tráfico".
  - `terminos` (list[tuple[str, float]]): pares término/% de la sección "Términos de búsqueda de YouTube" — tras el encabezado se saltan las líneas de cabecera ("Visualizaciones…", "Proporción de todo tu tráfico:" y su %), y se emparejan línea-término + línea-% (patrón `^\d+[.,]?\d* %$`) hasta "Ver más". Si la sección dice "No hay suficientes datos", lista vacía.
- `scrape_video_search(browser, video_id) -> dict`: navega a `tab-reach_viewers/period-default` del vídeo, mismo patrón de espera que retención (reintentos de 4 s; el dato clave es `terminos`, esperar hasta tenerlos o agotar 6 intentos).

### 2. Acumulador (`scripts/daily_analytics.py`)

- En el bucle nocturno, para los mismos candidatos de retención y con la misma sesión de navegador, llamar también a `scrape_video_search`.
- `busqueda_pct` se añade al registro cruzado (→ `analytics_daily.json`) y a la línea del histórico de retención.
- `.mp/search_terms.json` (nuevo, `SEARCH_TERMS_PATH`):

```json
{
  "updated": "2026-08-11T03:10",
  "terminos": {
    "atack on titan 3": {"apariciones": 3, "ultimo": "2026-08-11", "pct_max": 4.4}
  }
}
```

- Merge por noche: término visto → `apariciones += 1`, `ultimo = hoy`, `pct_max = max`. Los términos se normalizan solo con strip/lower (el typo ES el dato). Fallo del scrape de un vídeo → se ignora, nunca bloquea.

### 3. Consumo en metadatos (`src/classes/YouTube.py`, `generate_metadata`)

- `_matching_search_terms(subject, max_terms=5)`: lee `search_terms.json` y devuelve los términos con solapamiento de palabras significativas (normalizadas sin tildes, ≥4 letras) con el subject, ordenados por `apariciones`.
- Si hay coincidencias, tanto el prompt del título como el de la descripción reciben una línea extra: "REAL search queries people typed to find similar videos (misspellings are intentional, keep them if you use them): […]. If one fits naturally, work its wording into the text." — sugerencia, no obligación, para no forzar títulos artificiales.
- Sin coincidencias (o sin fichero) → prompts exactamente como hoy. Sin gate de idioma: el filtro de solapamiento ya descarta lo irrelevante (y las franquicias cruzan idiomas).

### 4. Informe semanal (`scripts/channel_report.py`, `main`)

- Cargar `search_terms.json`; si hay términos, añadir al prompt del análisis los 10 con más apariciones: "Términos de búsqueda reales que traen tráfico al canal: …" y pedir en el punto 2 que las recomendaciones consideren la demanda de búsqueda.

## Fuera de alcance

- Scraping de términos a nivel de canal (Studio Analytics global) — la vista por vídeo basta y ya la visitamos.
- Cambiar temas del harvester por búsquedas (el informe semanal ya realimenta `temas_ganadores`; no duplicar el canal de influencia).

## Verificación

- Test frío del parser contra el volcado real de la pestaña Cobertura (espera: busqueda_pct 71.9 y 5 términos con 4.4 %).
- Test frío de `_matching_search_terms` con un `search_terms.json` sintético.
- `py_compile` de los tres ficheros.
- E2E: nightly de esta noche — verificar mañana `search_terms.json` poblado y `busqueda_pct` en `analytics_daily.json`.
