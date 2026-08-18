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
