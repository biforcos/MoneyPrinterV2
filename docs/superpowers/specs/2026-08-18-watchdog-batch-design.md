# Watchdog de procesos nocturnos + timeout de Ollama

**Fecha:** 2026-08-18
**Contexto:** Dos incidentes de cuelgue total en 5 días. 14-08: ffmpeg de MoviePy
colgado tras el render, batch zombie 5h. 18-08: batch colgado 17h (sospechoso
principal: llamada al SDK de Ollama, que no tiene timeout por defecto) con efecto
dominó — 0 vídeos ese día, News 10:00 colgado 10h (también en llamada LLM) y los
slots de 15:00/17:30/20:00 muertos con exit 1 por el lock ocupado. Todas las
llamadas HTTP a ComfyUI ya tenían timeout; el agujero está en Ollama y en
cualquier cuelgue no-HTTP (ffmpeg, Selenium, TTS).

## Objetivo

Que un cuelgue cueste como mucho un vídeo, nunca un día de producción: cada
proceso del pipeline debe morir solo si excede su presupuesto de tiempo, dejando
el sistema en un estado que el siguiente paso del `.bat` pueda usar.

## Diseño

### Capa 1 — timeout en el cliente Ollama

`src/llm_provider.py`: `_client()` pasa `timeout=600` a `ollama.Client` (llega a
httpx). Un chat sano nunca tarda 10 min (peor caso real: guion con `think` en
qwen3:14b, pocos minutos); un Ollama wedged pasa de bloquear para siempre a
lanzar excepción, y el reintento existente del batch (2 intentos por vídeo +
aborto a los 3 fallos seguidos) toma el control.

### Capa 2 — watchdog de proceso (`scripts/watchdog.py`)

Clase `Watchdog(minutes, label, context_fn=None)`:

- Hilo daemon que comprueba el deadline cada 30 s.
- `reset()` rearma el deadline; `disarm()` lo desactiva.
- Al vencer, el hilo ejecuta en orden (best-effort cada paso):
  1. Imprime `[watchdog] <label>: límite de X min superado; matando proceso`
     con `flush=True`.
  2. Si hay `context_fn`, escribe su resultado en `logs/last_run.json`, lo
     añade a `logs/history.jsonl` y copia el fichero a
     `get_summary_export_path()` si está configurado (el briefing de Telegram
     lee la copia en WSL; sin esto vería un resumen obsoleto).
  3. Mata los procesos ComfyUI (`CommandLine like '*ComfyUI*main.py*'`, mismo
     patrón PowerShell que `--shutdown-comfyui`) para que el siguiente paso
     arranque uno fresco en vez de chocar con uno wedged en el puerto 8188.
  4. `taskkill /F /T /PID <propio>` — arrastra Firefox/ffmpeg hijos.

El lock de instancia única no necesita cambios: `acquire_single_instance_lock`
ya reclama locks cuyo PID está muerto.

### Integraciones

- **`scripts/batch_generate.py`**: watchdog de **90 min por vídeo**. Se arma
  antes de `ensure_local_providers()` (cubre bootstrap: select_model, TTS),
  `reset()` al inicio de cada iteración del bucle, `disarm()` al salir del
  bucle (antes del resumen final). `context_fn` devuelve el mismo dict de
  resumen que el `finally`, con `"status": "watchdog_timeout"` a nivel de run
  y los `results` acumulados hasta ese momento. Los vídeos reales tardan
  35-45 min incluida la carga de modelos; 90 min no da falsos positivos.
- **`scripts/news_harvester.py`**: watchdog **global de 30 min** (solo cosecha
  RSS + llamadas LLM, normalmente minutos), sin `context_fn`, `disarm()` al
  final. El vídeo del ciclo de news lo genera `batch_generate --news-only`,
  que ya queda cubierto por su propio watchdog.
- **`.bat`** (`nightly_batch.bat`, `news_cycle.bat`, `weekly_report.bat`):
  añadir `set PYTHONUNBUFFERED=1` para que las líneas de log no se pierdan en
  el buffer de bloque cuando un proceso muere (el 18-08 el log se quedó sin
  rastro del punto de cuelgue por esto).

## Riesgos aceptados

- Si el watchdog dispara durante una subida, el vídeo puede quedar publicado
  sin registrar en `youtube.json`. Con 90 min de margen es muy improbable;
  `reupload_failed.py` y la recuperación de progreso existente cubren el resto.
- `taskkill` del propio árbol no ejecuta `atexit` ni `finally` — por eso el
  propio watchdog escribe el resumen antes de matar.

## Testing

Tests de scratchpad (patrón de sesiones anteriores), sobre un subproceso
sacrificable — nunca sobre el runner:

1. Deadline vencido → el subproceso y sus hijos mueren y el resumen de
   `context_fn` queda escrito.
2. `reset()` extiende la vida más allá del deadline original.
3. `disarm()` desactiva el kill.
4. Timeout de Ollama: llamada real corta a `generate_text` sigue funcionando
   con el cliente con timeout.
