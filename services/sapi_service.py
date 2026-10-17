"""Voz local de Windows: SAPI aislado del hilo de Tk, sin paquetes adicionales.

``events`` entrega pares ``(nombre, payload)``. ``close`` inicia un cierre
asíncrono; la GUI puede esperar ``thread.is_alive()`` sin bloquear su bucle.
El trabajador purga la voz y, si el puente no responde, termina su proceso.
"""
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class VoiceService:
    """Una locución actual y como máximo una solicitud pendiente reemplazable."""

    STARTUP_TIMEOUT = 15.0
    MAX_TEXT_LENGTH = 20000

    def __init__(self, output_path=None):
        # Sólo para pruebas/exportación explícita: SAPI escribe un WAV local.
        # La ejecución normal (None) conserva la salida por altavoces.
        self._output_path = str(Path(output_path).resolve()) if output_path is not None else None
        self.events = queue.Queue()
        self._incoming = queue.Queue()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._pending = None
        self._stop_requested = False
        self._configuration = {"voice_id": None, "rate": 0, "volume": 100}
        self._configuration_dirty = True
        self._next_id = 0
        self._failure = None
        self._process = None
        self._reader = None
        self._close_watcher = None
        self._diagnostics = deque(maxlen=5)
        self.thread = threading.Thread(target=self._run, name="voz-sapi", daemon=True)
        self.thread.start()

    def configure(self, voice_id=None, rate=0, volume=100):
        """Ajustar voz instalada, velocidad SAPI (-10..10) y volumen (0..100)."""
        settings = {
            "voice_id": str(voice_id) if voice_id else None,
            "rate": max(-10, min(10, int(rate))),
            "volume": max(0, min(100, int(volume))),
        }
        with self._lock:
            self._configuration = settings
            self._configuration_dirty = True
        self._wake.set()

    def speak(self, text, label=""):
        """Hablar texto literal; reemplazar cualquier solicitud pendiente/activa."""
        with self._lock:
            self._next_id += 1
            request = {"request_id": self._next_id, "label": str(label), "text": str(text)}
            message = self._failure
            if self._closing.is_set():
                message = "El servicio de voz está cerrado."
            elif not request["text"].strip():
                message = "No hay texto para leer."
            elif len(request["text"]) > self.MAX_TEXT_LENGTH:
                message = "El texto supera el límite de 20000 caracteres."
            if message:
                self._emit("error", request, message)
            else:
                if self._pending:
                    self._emit("stopped", self._pending, "Reemplazado antes de iniciar.")
                self._pending = request
        self._wake.set()
        return request["request_id"]

    def stop(self):
        """Cancelar la lectura y la solicitud pendiente; no bloquea a Tk."""
        with self._lock:
            if self._pending:
                self._emit("stopped", self._pending, "Lectura cancelada.")
                self._pending = None
            self._stop_requested = True
        self._wake.set()

    def close(self):
        """Solicitar cierre; esperar a ``thread`` antes de darlo por terminado."""
        self.stop()
        with self._lock:
            if self._closing.is_set():
                return
            self._closing.set()
        self._wake.set()
        # También cubre un puente colgado que haya llenado su tubería stdin:
        # en ese caso el trabajador aún no puede alcanzar su bloque finally.
        self._close_watcher = threading.Thread(target=self._watch_close, name="voz-sapi-cierre", daemon=True)
        self._close_watcher.start()

    def _watch_close(self):
        self.thread.join(timeout=2.0)
        process = self._process
        if self.thread.is_alive() and process is not None and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=0.8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            except OSError:
                # Puede haber terminado entre poll() y terminate().
                if process.poll() is None:
                    self.events.put(("error", {"request_id": None, "label": "", "message": "Windows no permitió cerrar el motor de voz.", "fatal": True}))

    def _emit(self, event, request=None, message=None):
        payload = {"request_id": None, "label": ""}
        if request:
            payload.update({key: request.get(key, payload[key]) for key in payload})
        if message:
            payload["message"] = str(message)
        self.events.put((event, payload))

    def _command(self):
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        source = Path(__file__).with_name("sapi_bridge.ps1").read_text(encoding="utf-8")
        # El código es fijo y local. El texto a leer viaja exclusivamente por stdin
        # como JSON; nunca se concatena con comandos. No cambia ExecutionPolicy.
        return [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command", source]

    def _read_events(self, process):
        try:
            for line in process.stdout:
                try:
                    item = json.loads(line)
                    if isinstance(item, dict) and isinstance(item.get("payload"), dict):
                        self._incoming.put(item)
                        self._wake.set()
                    else:
                        self._diagnostics.append(line.strip()[:400])
                except (ValueError, TypeError):
                    self._diagnostics.append(line.strip()[:400])
        except (OSError, ValueError):
            pass
        finally:
            self._incoming.put({"event": "_eof", "payload": {}})
            self._wake.set()

    def _send(self, message):
        self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _fail(self, message, ready_seen):
        with self._lock:
            self._failure = message
            pending, self._pending = self._pending, None
        if not ready_seen:
            self.events.put(("ready", {"voices": [], "selected_id": None, "warning": message}))
        if pending:
            self._emit("error", pending, message)
        self.events.put(("error", {"request_id": None, "label": "", "message": message, "fatal": True}))

    def _run(self):
        ready_seen = False
        try:
            if sys.platform != "win32":
                raise RuntimeError("La voz local SAPI sólo está disponible en Windows.")
            if self._closing.is_set():
                return
            bridge_environment = os.environ.copy()
            bridge_environment.pop("LAB_VOICE_OUTPUT_PATH", None)
            if self._output_path is not None:
                bridge_environment["LAB_VOICE_OUTPUT_PATH"] = self._output_path
            self._process = subprocess.Popen(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW, env=bridge_environment,
            )
            self._reader = threading.Thread(
                target=self._read_events, args=(self._process,), name="voz-sapi-eventos", daemon=True,
            )
            self._reader.start()
            deadline = time.monotonic() + self.STARTUP_TIMEOUT
            while not self._closing.is_set():
                self._wake.clear()
                while True:
                    try:
                        item = self._incoming.get_nowait()
                    except queue.Empty:
                        break
                    event, payload = item["event"], item["payload"]
                    if event == "_eof":
                        detail = " ".join(self._diagnostics)
                        raise RuntimeError("El motor de voz se cerró inesperadamente." + (" " + detail if detail else ""))
                    if event == "ready":
                        ready_seen = True
                        self.events.put((event, payload))
                        if not payload.get("voices"):
                            with self._lock:
                                self._failure = payload.get("warning") or "No hay voces SAPI instaladas."
                                pending, self._pending = self._pending, None
                            if pending:
                                self._emit("error", pending, self._failure)
                            self.events.put(("error", {"request_id": None, "label": "", "message": self._failure, "fatal": True}))
                            return
                    elif event in {"started", "finished", "stopped", "error"}:
                        self.events.put((event, payload))
                        if event == "error" and payload.get("fatal"):
                            with self._lock:
                                self._failure = payload.get("message") or "El motor de voz no está disponible."
                            return
                if not ready_seen:
                    if time.monotonic() > deadline:
                        raise RuntimeError("El motor de voz no respondió al iniciar.")
                else:
                    with self._lock:
                        settings = dict(self._configuration) if self._configuration_dirty else None
                        self._configuration_dirty = False
                        stop_requested, self._stop_requested = self._stop_requested, False
                        pending, self._pending = self._pending, None
                    if stop_requested:
                        self._send({"command": "stop"})
                    if settings is not None:
                        self._send({"command": "configure", **settings})
                    if pending and not self._closing.is_set():
                        self._send({"command": "speak", **pending})
                self._wake.wait(0.04)
        except Exception as exc:
            if not self._closing.is_set():
                self._fail(str(exc), ready_seen)
        finally:
            self._shutdown_process()

    def _shutdown_process(self):
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    self._send({"command": "close"})
                    process.wait(timeout=0.8)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    process.terminate()
                    try:
                        process.wait(timeout=0.8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        finally:
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if self._reader:
                self._reader.join(timeout=0.3)
            if process.stdout:
                try:
                    process.stdout.close()
                except OSError:
                    pass
