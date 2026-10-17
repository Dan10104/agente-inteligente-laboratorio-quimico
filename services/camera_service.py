"""Un solo hilo posee VideoCapture; Tkinter nunca espera a la webcam."""
import logging
import queue
import threading
import time

import cv2
import config

logger = logging.getLogger(__name__)


def open_capture(index):
    for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
        cap = None
        try:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
                ok, frame = cap.read()
                if ok and frame is not None and frame.size:
                    return cap, frame
        except Exception:
            logger.exception("Falló el backend %s de la cámara %s", backend, index)
        if cap is not None:
            cap.release()
    raise RuntimeError(f"No se pudo abrir la cámara {index}. Compruebe conexión, permisos "
                       "de Windows y que otra aplicación no la esté usando.")


def detect_cameras(indices=config.CAMERA_INDICES, cancelled=None):
    available = []
    for index in indices:
        if cancelled and cancelled.is_set():
            break
        try:
            cap, _ = open_capture(index)
            available.append(index)
            cap.release()
        except RuntimeError:
            pass
    return available


class CameraService:
    def __init__(self):
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.sequence = 0
        self.fps = 0.0
        self.generation = 0
        self.thread = threading.Thread(target=self._run, name="camara", daemon=True)
        self.thread.start()

    def scan(self):
        self.commands.put(("scan", None))

    def start(self, index, request=0):
        self.commands.put(("start", (int(index), request)))

    def stop(self):
        self.commands.put(("stop", None))

    def close(self):
        self.stop_event.set()
        self.commands.put(("close", None))

    def snapshot(self):
        with self.lock:
            return (None if self.frame is None else self.frame.copy(), self.sequence,
                    self.fps, self.generation)

    def _clear(self):
        with self.lock:
            self.frame = None
            self.fps = 0.0
            self.generation += 1

    def _publish(self, frame, fps):
        with self.lock:
            self.frame = frame
            self.sequence += 1
            self.fps = fps

    def _run(self):
        cap = None
        selected = config.DEFAULT_CAMERA_INDEX
        request = 0
        active, failures, retries = False, 0, 0
        last = time.monotonic()
        try:
            while not self.stop_event.is_set():
                try:
                    command, value = self.commands.get(timeout=0.01 if active else 0.1)
                except queue.Empty:
                    command, value = None, None
                if command in ("stop", "start", "scan", "close"):
                    if cap is not None:
                        cap.release()
                        cap = None
                    active = False
                    self._clear()
                if command == "close":
                    break
                if command == "stop":
                    self.events.put(("stopped", "Cámara detenida"))
                elif command == "scan":
                    self.events.put(("status", "Buscando cámaras 0–3…"))
                    self.events.put(("cameras", detect_cameras(cancelled=self.stop_event)))
                elif command == "start":
                    selected, request = value
                    retries = 0
                    self.events.put(("status", f"Abriendo cámara {selected}…"))
                    try:
                        cap, frame = open_capture(selected)
                        active, failures = True, 0
                        last = time.monotonic()
                        self._publish(frame, 0.0)
                        self.events.put(("started", (selected, request)))
                    except Exception as exc:
                        logger.exception("Error al abrir cámara")
                        self.events.put(("error", str(exc)))
                if active and cap is not None:
                    try:
                        ok, frame = cap.read()
                    except cv2.error:
                        logger.exception("Error de lectura; se intentará reconectar")
                        ok, frame = False, None
                    now = time.monotonic()
                    if ok and frame is not None:
                        fps = 1.0 / max(now - last, 0.001)
                        self._publish(frame, self.fps * 0.8 + fps * 0.2)
                        last, failures = now, 0
                    else:
                        failures += 1
                        if failures >= 5:
                            cap.release()
                            cap = None
                            active = False
                            self._clear()
                            self.events.put(("disconnected", "Cámara desconectada; intentando reconectar…"))
                            while retries < config.CAMERA_MAX_RETRIES and not self.stop_event.wait(config.CAMERA_RETRY_SECONDS):
                                if not self.commands.empty():
                                    break
                                retries += 1
                                try:
                                    cap, frame = open_capture(selected)
                                    active, failures = True, 0
                                    last = time.monotonic()
                                    self._publish(frame, 0.0)
                                    self.events.put(("started", (selected, request)))
                                    retries = 0
                                    break
                                except RuntimeError:
                                    continue
                            if not active:
                                self.events.put(("error", "No se pudo recuperar la cámara. Puede reiniciarla o PROBAR IMAGEN."))
        except Exception as exc:
            logger.exception("Error del hilo de cámara")
            self.events.put(("error", str(exc)))
        finally:
            if cap is not None:
                cap.release()
            self._clear()
