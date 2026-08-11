# Evergreen desde demanda de búsqueda + vídeo relacionado

**Fecha:** 2026-08-11
**Estado:** aprobado

## Parte A — Temas evergreen desde `search_terms.json`

### Objetivo
Convertir la demanda de búsqueda medida en contenido: si un término acumula apariciones y no lo hemos cubierto hace poco, encolar automáticamente un tema evergreen en `topics.txt` (cola que `generate_topic` ya consume tras las noticias).

### Diseño (`scripts/daily_analytics.py`)
- Tras `merge_search_terms`, nueva función `propose_evergreen(data, cached_videos)`:
  - **Candidatos**: términos con `apariciones >= 3` y sin `propuesto` en los últimos 30 días.
  - **Cobertura**: se descarta si TODAS las palabras significativas del término (≥4 letras, sin tildes) tienen una palabra "cercana" (difflib, cutoff 0.8 — tolera typos: "atack"≈"attack") en el título de algún vídeo de los últimos 21 días.
  - **Volumen**: máximo 1 propuesta por noche, el candidato con más `apariciones`.
- El término elegido se convierte en tema con el LLM (ya cargado para los insights): "La gente busca en YouTube: '<term>'. Convierte esa búsqueda en un tema concreto y atractivo para un Short de videojuegos en español. Devuelve SOLO el tema, una línea." Sanitizado a una línea sin comillas.
- Se añade al FINAL de `topics.txt` (se crea si no existe) y se marca `propuesto: <fecha>` en la entrada del término en `search_terms.json`.
- El tema hereda todo el pipeline (metadatos con search_hint incluidos: el término solapará con el subject). Fallo del LLM → se omite esta noche, log de una línea.

### Orden de ejecución
`propose_evergreen` corre tras `refresh_insights` (el modelo ya está seleccionado con `select_model`).

## Parte B — Vídeo relacionado (`scripts/related_videos.py`, nuevo)

### Objetivo
Rellenar el campo "Vídeo relacionado" de los Shorts (hoy "Ninguno" en todos): un botón en la reproducción que lleva a otro vídeo del canal — sube el tiempo de sesión.

### Diseño
- Script nocturno (tras `comments_check.py` en `nightly_batch.bat`), Selenium con el perfil bot (reutiliza `open_studio_browser` de `channel_report`).
- Estado en `.mp/related_done.json`: `{video_id: {"related": <id|null>, "fecha": ...}}`. Se procesan vídeos públicos de los últimos 14 días del cache ES sin entrada, máximo 8 por noche.
- **Elección del relacionado**: otro vídeo público con mayor solapamiento de palabras significativas del título (misma franquicia); si no hay solape, el vídeo con mejor `retencion_pct` del histórico (vistas ≥5). Nunca el propio vídeo.
- **Flujo por vídeo**: abrir `studio.youtube.com/video/<id>/edit`, localizar la tarjeta "Vídeo relacionado"; si ya tiene uno → marcar hecho y seguir. Si "Ninguno": clic en el lápiz, buscar el vídeo elegido por título en el diálogo, seleccionarlo, guardar. Selectores exactos a fijar con un volcado en vivo (paso de descubrimiento, como el de retención).
- Todo envuelto por vídeo en try/except: un fallo loguea y pasa al siguiente; si el diálogo cambia de UI, el script nunca rompe el nightly.

## Fuera de alcance
- Evergreen para EN (los términos son del canal ES).
- Relacionado en el flujo de subida (tocarlo es más arriesgado que el barrido nocturno, que además cubre el backlog).

## Verificación
- Parte A: test frío de candidatos (umbral, cooldown, cobertura con typo); `py_compile`; E2E en el nightly.
- Parte B: descubrimiento en vivo + ejecución manual sobre 1-2 vídeos reales verificando en Studio que el campo queda puesto; luego al nightly.
