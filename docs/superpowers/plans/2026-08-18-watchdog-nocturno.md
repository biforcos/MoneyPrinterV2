# Watchdog nocturno + timeout Ollama — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un cuelgue en el pipeline nocturno cueste como mucho un vídeo, nunca un día de producción.

**Architecture:** Dos capas: (1) timeout de 600 s en el cliente Ollama (el sospechoso del cuelgue del 18-08); (2) clase `Watchdog` en `scripts/watchdog.py` — hilo daemon que, superado un presupuesto de tiempo, vuelca el resumen del run, mata ComfyUI y ejecuta `taskkill /F /T` sobre el propio proceso, dejando que el `.bat` continúe con el siguiente paso. Se integra por-vídeo en `batch_generate.py` (90 min) y global en `news_harvester.py` (30 min). Los `.bat` pasan a `PYTHONUNBUFFERED=1` para no perder líneas de log al morir un proceso.

**Tech Stack:** Python 3.12 (stdlib: threading, subprocess), Windows `taskkill`/PowerShell, unittest en scratchpad (el proyecto no tiene suite de tests).

**Spec:** `docs/superpowers/specs/2026-08-18-watchdog-batch-design.md`

## Global Constraints

- Proyecto sin suite de tests ni CI: los tests van al scratchpad de la sesión, referido aquí como `SCRATCH` = `C:\Users\bifor\AppData\Local\Temp\claude\C--Users-bifor-Documents-Proyectos-MoneyPrinterV2\43eb388b-a01d-4718-bc91-70e34c79265c\scratchpad`.
- Ejecutar siempre desde la raíz del proyecto (`C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2`) con `venv\Scripts\python.exe`.
- Los scripts de `scripts/` se importan entre sí por nombre pelado (`sys.path[0]` es `scripts/` al ejecutarlos) y añaden `src/` a `sys.path` vía `ROOT`.
- Comentarios y strings de log en español, siguiendo el estilo existente (`[batch]`, `[news]`, `[watchdog]`).
- Los tests del watchdog usan SIEMPRE un subproceso sacrificable (`taskkill /F /T` mata el árbol entero del proceso que lo llama — nunca armarlo en el runner de tests). Y siempre `kill_comfyui=False` en tests para no matar un ComfyUI real.
- Commits pequeños, uno por task, mensajes en español como el historial (`git log --oneline`).

---

### Task 1: Timeout en el cliente Ollama

**Files:**
- Modify: `src/llm_provider.py` (función `_client`, ~línea 10)
- Test: `SCRATCH\test_ollama_timeout.py`

**Interfaces:**
- Consumes: `ollama.Client(host=..., timeout=...)` (el kwarg `timeout` llega al `httpx.Client` interno).
- Produces: `_client()` devuelve un cliente con `timeout=600`. Nada más cambia; `generate_text`/`list_models`/`unload_model` lo heredan.

- [ ] **Step 1: Write the failing test**

```python
# SCRATCH\test_ollama_timeout.py
import sys
import unittest

sys.path.insert(0, r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2\src")

import llm_provider


class TestOllamaTimeout(unittest.TestCase):
    def test_client_lleva_timeout(self):
        captured = {}
        original = llm_provider.ollama.Client

        class Spy(original):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

        llm_provider.ollama.Client = Spy
        try:
            llm_provider._client()
        finally:
            llm_provider.ollama.Client = original
        self.assertEqual(captured.get("timeout"), 600)

    def test_list_models_sigue_funcionando(self):
        # Llamada real barata contra el Ollama local (/api/tags)
        models = llm_provider.list_models()
        self.assertIsInstance(models, list)
        self.assertTrue(models)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe SCRATCH\test_ollama_timeout.py`
Expected: FAIL en `test_client_lleva_timeout` (`captured.get("timeout")` es `None`). `test_list_models_sigue_funcionando` debe pasar ya (baseline de que Ollama está vivo).

- [ ] **Step 3: Write minimal implementation**

En `src/llm_provider.py`, sustituir:

```python
def _client() -> ollama.Client:
    return ollama.Client(host=get_ollama_base_url())
```

por:

```python
def _client() -> ollama.Client:
    # Sin timeout, un Ollama wedged bloquea el proceso para siempre
    # (incidente 2026-08-18); 10 min cubren de sobra el peor chat real
    # (guion con think en qwen3:14b).
    return ollama.Client(host=get_ollama_base_url(), timeout=600)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe SCRATCH\test_ollama_timeout.py`
Expected: PASS (2 tests OK)

- [ ] **Step 5: Commit**

```bash
git add src/llm_provider.py
git commit -m "Timeout de 10 min en el cliente Ollama"
```

---

### Task 2: Clase Watchdog (`scripts/watchdog.py`)

**Files:**
- Create: `scripts/watchdog.py`
- Create: `SCRATCH\wd_victim.py` (proceso sacrificable para los tests)
- Test: `SCRATCH\test_watchdog.py`

**Interfaces:**
- Consumes: stdlib solamente (threading, subprocess, json, os, shutil, time).
- Produces: `Watchdog(minutes: float, label: str, context_fn: Callable[[], dict] | None = None, export_path: str = "", root: str = ROOT, check_every: float = 30, kill_comfyui: bool = True)` con métodos `reset() -> None` y `disarm() -> None`. Al vencer el deadline: log `[watchdog]` con flush, vuelca `context_fn()` en `<root>/logs/last_run.json` + append a `<root>/logs/history.jsonl` + copia a `export_path` si no está vacío, mata procesos ComfyUI si `kill_comfyui`, y ejecuta `taskkill /F /T /PID <propio>` seguido de `os._exit(1)` como cinturón.

- [ ] **Step 1: Write the failing tests**

```python
# SCRATCH\wd_victim.py
"""Proceso sacrificable: arma un Watchdog y según el modo se cuelga,
resetea o desarma. Uso: wd_victim.py <hang|reset|disarm> <workdir>"""
import os
import subprocess
import sys
import time

sys.path.insert(0, r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2\scripts")
from watchdog import Watchdog

mode, workdir = sys.argv[1], sys.argv[2]

# Nieto que debe morir con el árbol en modo hang
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"]
)
with open(os.path.join(workdir, "grandchild.pid"), "w") as fh:
    fh.write(str(grandchild.pid))

wd = Watchdog(
    minutes=4 / 60,  # deadline de 4 segundos
    label="victim",
    context_fn=lambda: {"status": "watchdog_timeout", "marker": "victim"},
    root=workdir,
    check_every=1,
    kill_comfyui=False,
)

if mode == "hang":
    time.sleep(120)  # el watchdog debe matarnos
elif mode == "reset":
    for _ in range(3):  # 9s de "trabajo" > 4s de deadline, con resets
        time.sleep(3)
        wd.reset()
    wd.disarm()
    grandchild.kill()
    sys.exit(0)
elif mode == "disarm":
    time.sleep(1)
    wd.disarm()
    time.sleep(8)  # sobrevive pasado el deadline original
    grandchild.kill()
    sys.exit(0)
```

```python
# SCRATCH\test_watchdog.py
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRATCH = os.path.dirname(os.path.abspath(__file__))
VICTIM = os.path.join(SCRATCH, "wd_victim.py")
PYTHON = r"C:\Users\bifor\Documents\Proyectos\MoneyPrinterV2\venv\Scripts\python.exe"


def pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
    ).stdout
    return str(pid) in out


def run_victim(mode: str, workdir: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, VICTIM, mode, workdir],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestWatchdog(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="wd_test_")

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _grandchild_pid(self) -> int:
        with open(os.path.join(self.workdir, "grandchild.pid")) as fh:
            return int(fh.read())

    def test_hang_mata_arbol_y_vuelca_resumen(self):
        # El victim duerme 120s con deadline de 4s: debe morir en ~15s
        proc = run_victim("hang", self.workdir, timeout=40)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("[watchdog]", proc.stdout)
        self.assertFalse(pid_alive(self._grandchild_pid()))
        with open(os.path.join(self.workdir, "logs", "last_run.json")) as fh:
            summary = json.load(fh)
        self.assertEqual(summary["status"], "watchdog_timeout")
        self.assertEqual(summary["marker"], "victim")
        with open(os.path.join(self.workdir, "logs", "history.jsonl")) as fh:
            self.assertIn("watchdog_timeout", fh.read())

    def test_reset_extiende_el_deadline(self):
        # 9s de trabajo con resets cada 3s sobre deadline de 4s: sobrevive
        proc = run_victim("reset", self.workdir, timeout=40)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("[watchdog]", proc.stdout)

    def test_disarm_desactiva_el_kill(self):
        # disarm a 1s, luego duerme 8s (> deadline de 4s): sobrevive
        proc = run_victim("disarm", self.workdir, timeout=40)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("[watchdog]", proc.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv\Scripts\python.exe SCRATCH\test_watchdog.py`
Expected: los 3 tests FAIL/ERROR con `ModuleNotFoundError: No module named 'watchdog'` (en el stderr del victim; el runner verá returncode 1 en modos reset/disarm y el assert de last_run.json fallará en hang).

- [ ] **Step 3: Write the implementation**

```python
# scripts/watchdog.py
"""Watchdog de proceso para los scripts nocturnos.

Si el proceso supera su presupuesto de tiempo (cuelgues de Ollama, ffmpeg,
Selenium... — incidentes del 14-08 y 18-08), un hilo daemon vuelca el resumen
del run, mata ComfyUI para que el siguiente paso del .bat arranque uno fresco,
y ejecuta taskkill /F /T sobre el propio árbol (arrastra Firefox/ffmpeg hijos).
El lock de instancia única de batch_generate se auto-reclama al estar el PID
muerto, así que no hace falta limpiarlo aquí.
"""
import json
import os
import shutil
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_KILL_COMFYUI_PS = (
    "Get-CimInstance Win32_Process | "
    "Where-Object { $_.CommandLine -like '*ComfyUI*main.py*' } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)


class Watchdog:
    def __init__(
        self,
        minutes: float,
        label: str,
        context_fn=None,
        export_path: str = "",
        root: str = ROOT,
        check_every: float = 30,
        kill_comfyui: bool = True,
    ) -> None:
        self.minutes = minutes
        self.label = label
        self.context_fn = context_fn
        self.export_path = export_path
        self.root = root
        self.check_every = check_every
        self.kill_comfyui = kill_comfyui
        self._lock = threading.Lock()
        self._armed = True
        self._deadline = time.monotonic() + minutes * 60
        self._thread = threading.Thread(
            target=self._watch, name=f"watchdog-{label}", daemon=True
        )
        self._thread.start()

    def reset(self) -> None:
        """Rearma el deadline (llamar al empezar cada unidad de trabajo)."""
        with self._lock:
            self._deadline = time.monotonic() + self.minutes * 60

    def disarm(self) -> None:
        """Desactiva el watchdog (llamar al terminar el trabajo vigilado)."""
        with self._lock:
            self._armed = False

    def _watch(self) -> None:
        while True:
            time.sleep(self.check_every)
            with self._lock:
                if not self._armed:
                    return
                expired = time.monotonic() >= self._deadline
            if expired:
                self._fire()
                return

    def _fire(self) -> None:
        print(
            f"[watchdog] {self.label}: límite de {self.minutes:g} min "
            "superado; matando el proceso y su árbol",
            flush=True,
        )
        if self.context_fn is not None:
            try:
                summary = self.context_fn()
                logs_dir = os.path.join(self.root, "logs")
                os.makedirs(logs_dir, exist_ok=True)
                last_run = os.path.join(logs_dir, "last_run.json")
                with open(last_run, "w", encoding="utf-8") as fh:
                    json.dump(summary, fh, ensure_ascii=False, indent=2)
                with open(
                    os.path.join(logs_dir, "history.jsonl"), "a", encoding="utf-8"
                ) as fh:
                    fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
                if self.export_path:
                    shutil.copy2(last_run, self.export_path)
            except Exception as e:
                print(f"[watchdog] No se pudo volcar el resumen: {e}", flush=True)
        if self.kill_comfyui:
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", _KILL_COMFYUI_PS],
                    check=False,
                    timeout=60,
                )
            except Exception:
                pass
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(os.getpid())], check=False
        )
        os._exit(1)  # cinturón por si taskkill fallara
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv\Scripts\python.exe SCRATCH\test_watchdog.py`
Expected: PASS (3 tests OK, ~30-40 s por los sleeps)

- [ ] **Step 5: Commit**

```bash
git add scripts/watchdog.py
git commit -m "Watchdog de proceso para los scripts nocturnos"
```

---

### Task 3: Integración en `batch_generate.py` (90 min por vídeo)

**Files:**
- Modify: `scripts/batch_generate.py` (imports ~línea 48, `main()` ~líneas 138-160, bucle ~línea 186, `finally` ~línea 257)

**Interfaces:**
- Consumes: `Watchdog` de Task 2 (firma exacta de su docstring de Interfaces).
- Produces: nada nuevo para otros tasks; `last_run.json` gana el valor de estado `"status": "watchdog_timeout"` a nivel de run cuando dispara el watchdog.

- [ ] **Step 1: Añadir import**

Tras `from utils import rem_temp_files` (línea 48):

```python
from watchdog import Watchdog
```

- [ ] **Step 2: Armar el watchdog al inicio de `main()`**

Sustituir (líneas ~138-141):

```python
    args = parser.parse_args()

    acquire_single_instance_lock()
    ensure_local_providers()
```

por:

```python
    args = parser.parse_args()

    started = datetime.now()
    results = []
    run_state = {"account": ""}

    def watchdog_summary():
        return {
            "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now().isoformat(timespec="seconds"),
            "account": run_state["account"],
            "status": "watchdog_timeout",
            "produced": sum(
                1 for r in results if r.get("status") != "generation_failed"
            ),
            "uploaded": sum(
                1 for r in results if r.get("status") == "uploaded"
            ),
            "videos": results,
        }

    # 90 min por vídeo (los reales tardan 35-45 min con carga de modelos);
    # también cubre el bootstrap (select_model, TTS) hasta el primer reset.
    watchdog = Watchdog(
        minutes=90,
        label="batch_generate",
        context_fn=watchdog_summary,
        export_path=get_summary_export_path().strip(),
    )

    acquire_single_instance_lock()
    ensure_local_providers()
```

- [ ] **Step 3: Registrar la cuenta elegida y quitar las variables duplicadas**

Tras la selección de cuenta (después de `account = accounts[0]` / la rama `--account`, junto al print `[batch] Cuenta:`), añadir:

```python
    run_state["account"] = account["nickname"]
```

Y sustituir (líneas ~156-159):

```python
    tts = TTS()
    produced, uploaded, consecutive_failures = 0, 0, 0
    started = datetime.now()
    results = []
```

por:

```python
    tts = TTS()
    produced, uploaded, consecutive_failures = 0, 0, 0
```

(`started` y `results` ya se definieron antes de armar el watchdog.)

- [ ] **Step 4: Reset por vídeo y disarm al salir**

Al inicio de cada iteración, justo después de la línea `print(f"\n[batch] ===== Vídeo {iteration}...")` y antes de `rem_temp_files()`:

```python
            watchdog.reset()
```

Y en el `finally` del bucle principal (línea ~257), como PRIMERA línea del bloque:

```python
    finally:
        watchdog.disarm()
        # Machine-readable summary for external reporting (Telegram
        ...
```

- [ ] **Step 5: Verify compilation and smoke test**

Run: `venv\Scripts\python.exe -m py_compile scripts\batch_generate.py`
Expected: sin salida (OK)

Run (smoke real, ~2-3 min; arranca ComfyUI, no genera nada al no haber noticias pendientes, y lo apaga):
`venv\Scripts\python.exe scripts\batch_generate.py --max 1 --news-only --no-upload --shutdown-comfyui`
Expected: llega a `[batch] No quedan noticias pendientes; fin del ciclo.` y termina con `[batch] Resumen: 0 generados, 0 subidos.` sin disparar el watchdog. Si SÍ hay noticias pendientes en `.mp/news_queue.json`, saltar este smoke (generaría un vídeo real) y validar solo con py_compile.

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_generate.py
git commit -m "Watchdog de 90 min por video en el batch"
```

---

### Task 4: Integración en `news_harvester.py` (30 min global)

**Files:**
- Modify: `scripts/news_harvester.py` (import ~línea 32, `main()` ~líneas 229-268)

**Interfaces:**
- Consumes: `Watchdog` de Task 2.
- Produces: nada nuevo.

- [ ] **Step 1: Añadir import**

Tras `from llm_provider import select_model, generate_text` (línea 32):

```python
from watchdog import Watchdog
```

- [ ] **Step 2: Armar y desarmar**

En `main()`, justo después de `args = parser.parse_args()` (línea ~229):

```python
    # La cosecha + redactor jefe tarda minutos; 30 min = cuelgue seguro
    # (el 18-08 estuvo 10h bloqueado en una llamada LLM).
    watchdog = Watchdog(minutes=30, label="news_harvester", kill_comfyui=False)
```

Y como última línea de `main()`, tras el `print(f"[news] {len(items)} noticias añadidas...")` (línea ~268):

```python
    watchdog.disarm()
```

Nota: `main()` tiene returns tempranos si no hay candidatas o el redactor no elige ninguna — comprobar con `Grep "return" scripts/news_harvester.py` y añadir `watchdog.disarm()` antes de cada `return` de `main()` (o envolver el cuerpo en `try/finally: watchdog.disarm()`, lo que quede más limpio con el código real).

- [ ] **Step 3: Verify compilation and smoke**

Run: `venv\Scripts\python.exe -m py_compile scripts\news_harvester.py`
Expected: sin salida (OK)

No ejecutar el harvester real (encolaría una noticia fuera de ciclo).

- [ ] **Step 4: Commit**

```bash
git add scripts/news_harvester.py
git commit -m "Watchdog global de 30 min en el news harvester"
```

---

### Task 5: `PYTHONUNBUFFERED=1` en los `.bat`

**Files:**
- Modify: `scripts/nightly_batch.bat`, `scripts/news_cycle.bat`, `scripts/weekly_report.bat`

**Interfaces:** N/A (solo entorno).

- [ ] **Step 1: Añadir la variable en los tres .bat**

En cada fichero, tras la línea `set PYTHONIOENCODING=utf-8`, añadir:

```bat
set PYTHONUNBUFFERED=1
```

(Motivo en comentario NO necesario — los .bat ya son escuetos; el spec documenta el porqué.)

- [ ] **Step 2: Verificar**

Run: `Select-String -Path scripts\*.bat -Pattern "PYTHONUNBUFFERED"`
Expected: 3 coincidencias (nightly_batch, news_cycle, weekly_report).

- [ ] **Step 3: Commit**

```bash
git add scripts/nightly_batch.bat scripts/news_cycle.bat scripts/weekly_report.bat
git commit -m "Logs sin buffer en las tareas programadas"
```

---

### Task 6: Verificación final

**Files:** ninguno nuevo.

- [ ] **Step 1: Re-run de todos los tests**

Run: `venv\Scripts\python.exe SCRATCH\test_ollama_timeout.py` y `venv\Scripts\python.exe SCRATCH\test_watchdog.py`
Expected: 5 tests PASS en total.

- [ ] **Step 2: py_compile de todo lo tocado**

Run: `venv\Scripts\python.exe -m py_compile scripts\watchdog.py scripts\batch_generate.py scripts\news_harvester.py src\llm_provider.py`
Expected: sin salida (OK).

- [ ] **Step 3: Comprobar que no queda ComfyUI huérfano de los smokes**

Run: `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ComfyUI*main.py*' }`
Expected: sin resultados (el smoke de Task 3 lo apagó con `--shutdown-comfyui`; si queda alguno, matarlo).

- [ ] **Step 4: Estado limpio de git**

Run: `git status` y `git log --oneline -6`
Expected: working tree limpio; los 5 commits del plan (spec aparte) encima de `2538aea`.
