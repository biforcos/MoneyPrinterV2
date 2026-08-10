# A/B de duración de guion

**Fecha:** 2026-08-10
**Estado:** aprobado

## Objetivo

Medir si los vídeos cortos retienen mejor, con un A/B limpio por vídeo: guion corto (3-4 frases) vs normal (5-7 frases). La retención media actual (~18 s vistos en vídeos de 24-43 s) sugiere que la gente se va a mitad. Es el pendiente apuntado en el roadmap ("acortar vídeos cuando el A/B del loop tenga veredicto"), desbloqueado el 9-10 de agosto.

## Diseño

1. **Config** (`config.json` + `config.example.json` + getter en `src/config.py`):
   - Nueva clave `short_script_ratio` (0.0-1.0). `get_short_script_ratio()` con default **0.0** (apagado si no se configura), patrón idéntico a `get_loop_ending_ratio`. En `config.json` local se fija a **0.5**.
   - `script_sentence_length_range` pasa de `[4,7]` a `[5,7]` en `config.json` para que los grupos sean disjuntos (corto 3-4 vs normal 5-7).

2. **`YouTube.generate_script`** (`src/classes/YouTube.py:518`): antes de elegir la longitud, moneda al aire `self._short_script = random.random() < get_short_script_ratio()`. Si sale corto, `sentence_length = random.randint(3, 4)`; si no, la lógica actual (rango de config o valor forzado). `self._sentence_length` sigue alimentando el número de imágenes, sin cambios.

3. **`YouTube.combine`**: tras montar `final_clip` (justo antes de `write_videofile`), guardar `self._video_duration = round(final_clip.duration, 1)` — la duración real es mejor correlato que el número de frases.

4. **`add_video`** (llamada en el upload, `src/classes/YouTube.py:2635`): dos campos nuevos en el registro:
   - `"short_script": bool(getattr(self, "_short_script", False))`
   - `"dur_video_s": getattr(self, "_video_duration", None)`

5. **`scripts/daily_analytics.py`**: `short_script` se añade al A/B de vistas/día (dict `readouts`) y a `retention_fields` del A/B de retención. `retention_readout` y `ab_readout` ya son genéricos: no necesitan cambios.

## Fuera de alcance

- Cambiar la duración por defecto: si el corto gana tras ~1 semana de datos, se consolidará bajando el rango normal (decisión aparte).
- Overrides por cuenta (ninguna cuenta los usa hoy; la clave global aplica a ES y EN).

## Verificación

- `py_compile` de `src/config.py`, `src/classes/YouTube.py`, `scripts/daily_analytics.py`.
- Test frío: `get_short_script_ratio()` devuelve 0.5 con el config actual y 0.0 si la clave falta.
- Validación E2E real: el nightly de esta noche (3:00) genera con el A/B activo; verificar mañana que `youtube.json` registra `short_script` y `dur_video_s`.
