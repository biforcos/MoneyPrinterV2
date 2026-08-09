# Retención por vídeo en el bucle de analíticas

**Fecha:** 2026-08-09
**Estado:** aprobado (enfoque A: scraping de la página de analytics por vídeo)

## Objetivo

Incorporar dos métricas de retención por Short al bucle diario de analíticas:

- **% de espectadores que siguieron viendo** (no hicieron swipe en los primeros segundos) — mide el hook.
- **Duración media de visualización** — mide el pacing.

Hoy todo el sistema (A/B de loop endings, temas ganadores, informe semanal) optimiza sobre vistas, una señal muy ruidosa con vídeos de 1–200 vistas. La retención es densa e independiente del número de vistas, así que da señal útil incluso en vídeos con 20 visualizaciones.

## Contexto actual

- `scripts/channel_report.py` → `scrape_shorts()`: Firefox headless con el perfil bot, lee la lista de Shorts en Studio (`ytcp-video-row`) parseando `row.text`. Devuelve título, visibilidad, fecha, vistas y comentarios.
- `scripts/daily_analytics.py`: cruza esas filas con `.mp/youtube.json` por prefijo de título normalizado, calcula A/B de vistas/día por `loop_ending` y `news`, refresca `temas_ganadores` y escribe `logs/analytics_daily.json`. Corre en el batch nocturno antes de la generación.
- Los registros de `.mp/youtube.json` tienen `url` con el ID de vídeo (`v=<ID>`), lo que permite construir la URL de analytics por vídeo sin depender de la lista.

## Diseño

### 1. `scrape_video_retention(browser, video_id)` en `channel_report.py`

Nueva función que, con el navegador ya autenticado:

1. Navega a `https://studio.youtube.com/video/<video_id>/analytics/tab-overview/period-default`.
2. Espera a que cargue (patrón de reintentos con `time.sleep`, como `scrape_shorts`).
3. Extrae del texto de la página (mismo estilo de parseo por texto, no por selectores frágiles):
   - `retencion_pct` (float): el porcentaje junto a la etiqueta de "siguieron viendo" / "stayed to watch".
   - `dur_media_s` (int): la duración media en segundos, parseando formato `M:SS`.
4. Devuelve un dict `{"retencion_pct": ..., "dur_media_s": ...}` con `None` en lo que no encuentre.

**Paso de descubrimiento en implementación:** antes de fijar el parseo, volcar el texto completo de la página de un vídeo real para identificar las etiquetas exactas en español y su formato. El parseo se escribe contra ese volcado.

### 2. Integración en `daily_analytics.py`

- Tras `cross_stats()`, seleccionar los vídeos a scrapear: **públicos, ≤14 días de antigüedad, máximo 15 por noche** (los más recientes primero). Solo cuenta ES (`accounts[0]`).
- Reutilizar una única sesión de navegador para lista + N páginas de vídeo. Refactor en `channel_report.py`: extraer `open_studio_browser()` y cambiar `scrape_shorts(browser=None)` para que acepte un navegador ya abierto (si es `None`, abre y cierra el suyo — compatibilidad con el informe semanal sin tocarlo). `daily_analytics.py` abre el navegador una vez, lee la lista, cruza, selecciona y scrapea las páginas de retención, y cierra al final.
- Añadir `retencion_pct` y `dur_media_s` a los registros cruzados que ya se escriben en `logs/analytics_daily.json`.
- Nuevo readout A/B por retención: mediana de `retencion_pct` por `loop_ending`, `news`, `mood` y `dialogue`. Mantener `MIN_AGE_HOURS = 24` también aquí (consistencia y estabilización de la métrica).
- Coste estimado: ~8 s/vídeo × ≤15 vídeos ≈ 2 min extra en el batch nocturno. Aceptado.

### 3. Histórico: `logs/retention_history.jsonl`

Una línea por vídeo y noche:

```json
{"fecha": "2026-08-09", "video_id": "nYJcHgG0W1I", "titulo": "...", "retencion_pct": 62.1, "dur_media_s": 21, "vistas": 6}
```

Append-only. Permite ver la evolución de la retención de un mismo vídeo y comparar cohortes (p. ej., vídeos con hook overlay del 8 de agosto en adelante vs. anteriores).

### 4. Informe semanal (`channel_report.py`)

La tabla que se pasa al LLM incluye retención y duración media cuando estén disponibles (leyéndolas del último `analytics_daily.json`, sin re-scrapear), y el prompt pide explícitamente patrones de retención además de los de vistas.

## Manejo de errores

- Fallo al scrapear un vídeo → log de una línea y continuar con el siguiente; el registro queda sin métricas de retención (`None`).
- Cero vídeos con retención → se omiten el readout A/B de retención y el append al histórico; el resto del bucle diario sigue igual (nunca bloquea vistas/insights).
- Etiquetas no encontradas en la página (cambio de UI de Studio) → mismo tratamiento que fallo de scrapeo; el log debe dejar claro que fue el parseo, para detectarlo en el briefing.

## Pruebas

1. Volcado de texto de una página de analytics real → fixture para desarrollar el parseo en frío (sin navegador).
2. Test del parseo contra el fixture (porcentaje, duración `M:SS`, etiquetas ausentes).
3. Ejecución manual completa de `daily_analytics.py` con el perfil bot (ventana cerrada) verificando: métricas en `analytics_daily.json`, líneas en `retention_history.jsonl`, readouts A/B impresos.
4. `py_compile` de los dos scripts antes de commit.

## Fuera de alcance

- Canal EN (1 vídeo; se extiende cuando tenga volumen — la selección por `accounts[0]` deja el hueco preparado).
- Curva de retención completa (gráfica SVG, frágil de scrapear).
- YouTube Analytics API (OAuth fuera del patrón de perfil Firefox).
- Decisiones automáticas sobre la retención (primero acumular datos; las decisiones siguen siendo del briefing/LLM semanal).
