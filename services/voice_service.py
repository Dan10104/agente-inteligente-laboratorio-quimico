"""Voz neuronal española: caché, cancelación y SAPI opcional fuera del hilo Tk."""
import asyncio
import ctypes
import logging
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
from urllib.parse import urlencode, urlsplit

import config
from services.sapi_service import VoiceService as SapiVoiceService
from services.speech_cache import SpeechCache
from services.speech_text import prepare_text_for_speech


EDGE_VOICES = (
    {"id": "es-MX-DaliaNeural", "name": "Dalia - Español México", "language": "es-MX", "spanish": True, "engine": "edge"},
    {"id": "es-MX-JorgeNeural", "name": "Jorge - Español México", "language": "es-MX", "spanish": True, "engine": "edge"},
    {"id": "es-ES-ElviraNeural", "name": "Elvira - Español España", "language": "es-ES", "spanish": True, "engine": "edge"},
    {"id": "es-ES-AlvaroNeural", "name": "Álvaro - Español España", "language": "es-ES", "spanish": True, "engine": "edge"},
)


async def synthesize_edge(text, voice, rate, volume, pitch, destination):
    """Importación diferida: una lectura con caché no necesita conectar a Internet."""
    import edge_tts
    from edge_tts import communicate as transport, constants
    _adapt_edge_endpoint(transport, constants.TRUSTED_CLIENT_TOKEN)
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
    await communicate.save(str(destination))


def _adapt_edge_endpoint(transport, client_token):
    # edge-tts 7.2.8 aún usa el host de síntesis retirado en octubre de 2026.
    # Adaptación del cambio propuesto por el proyecto: rany2/edge-tts#491
    # https://github.com/rany2/edge-tts/pull/491
    # Sólo cambia la constante en memoria si sigue apuntando al host antiguo;
    # versiones futuras conservan su URL oficial y ningún paquete se reescribe.
    current = urlsplit(getattr(transport, "WSS_URL", ""))
    if current.scheme == "wss" and current.hostname == "speech.platform.bing.com":
        transport.WSS_URL = (
            "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?"
            + urlencode({"Ocp-Apim-Subscription-Key": client_token})
        )


class MCIPlayer:
    """Reproductor MP3 nativo winmm/MCI usado exclusivamente por el trabajador."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("La reproducción local requiere Windows.")
        self._api = ctypes.WinDLL("winmm")
        self._api.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self._api.mciSendStringW.restype = ctypes.c_uint
        self._api.mciGetErrorStringW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        self._api.mciGetErrorStringW.restype = ctypes.c_bool
        self._alias = "labtts_" + uuid.uuid4().hex
        self._opened = False
        self._deadline = None

    def _command(self, command):
        buffer = ctypes.create_unicode_buffer(512)
        result = self._api.mciSendStringW(command, buffer, len(buffer), None)
        if result:
            self._api.mciGetErrorStringW(result, buffer, len(buffer))
            raise RuntimeError(f"Windows no pudo reproducir la voz: {buffer.value} (MCI {result}).")
        return buffer.value.strip()

    def start(self, path):
        path = str(Path(path).resolve())
        if '"' in path or "\n" in path or "\r" in path:
            raise ValueError("Ruta de audio no válida.")
        self._command(f'open "{path}" type mpegvideo alias {self._alias}')
        self._opened = True
        self._command(f"set {self._alias} time format milliseconds")
        duration = int(self._command(f"status {self._alias} length")) / 1000
        self._command(f"play {self._alias} from 0")
        self._deadline = time.monotonic() + max(duration + 10, 30)

    def finished(self):
        mode = self._command(f"status {self._alias} mode").lower()
        if mode == "stopped":
            return True
        if mode not in {"playing", "seeking"}:
            raise RuntimeError(f"El reproductor de voz dejó de estar disponible ({mode}).")
        if time.monotonic() > self._deadline:
            raise RuntimeError("El reproductor de voz no finalizó en el tiempo esperado.")
        return False

    def close(self):
        if self._opened:
            try:
                self._command(f"stop {self._alias}")
            finally:
                try:
                    self._command(f"close {self._alias}")
                finally:
                    self._opened = False


class VoiceService:
    """Una lectura y una petición pendiente; close es asíncrono e idempotente."""

    MAX_TEXT_LENGTH = 20000

    def __init__(self, output_path=None, *, cache_dir=None):
        self.events = queue.Queue()
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._pending = self._current = self._active_task = None
        self._loop = self._wake = self._local_ready = None
        self._next_id = 0
        self._latest_id = None
        self._failure = None
        self._local = None
        self._local_voices = []
        self._local_warning = ""
        self._local_waiters = {}
        self._output_path = output_path
        self._cache_dir = cache_dir or getattr(config, "TTS_CACHE_DIR", config.BASE_DIR / "cache" / "tts")
        self._cache = None
        self._settings = {"engine": getattr(config, "TTS_ENGINE", "edge"),
                          "voice_id": getattr(config, "EDGE_VOICE", "es-MX-DaliaNeural"),
                          "rate": 0, "volume": 100}
        self.thread = threading.Thread(target=self._run, name="voz-neuronal", daemon=True)
        self.thread.start()

    def configure(self, voice_id=None, rate=0, volume=100, engine=None):
        with self._lock:
            selected_engine = engine or self._settings["engine"]
            if engine is None and voice_id:
                selected_engine = "edge" if voice_id in {item["id"] for item in EDGE_VOICES} else "sapi"
            if selected_engine not in {"edge", "sapi"}:
                raise ValueError("El motor de voz debe ser 'edge' o 'sapi'.")
            if not voice_id:
                if selected_engine == self._settings["engine"]:
                    voice_id = self._settings["voice_id"]
                elif selected_engine == "edge":
                    voice_id = getattr(config, "EDGE_VOICE", "es-MX-DaliaNeural")
            self._settings = {"engine": selected_engine, "voice_id": voice_id,
                              "rate": rate, "volume": max(0, min(100, int(volume)))}

    def speak(self, text, label=""):
        with self._lock:
            self._next_id += 1
            request = {"request_id": self._next_id, "label": str(label), "text": str(text),
                       "settings": dict(self._settings), "cancel": threading.Event(), "terminal": False}
            if self._failure:
                self._emit("error", request, self._failure)
            elif self._closing.is_set():
                self._emit("error", request, "El servicio de voz está cerrado.")
            elif not request["text"].strip():
                self._emit("error", request, "No hay texto para leer.")
            elif len(request["text"]) > self.MAX_TEXT_LENGTH:
                self._emit("error", request, "El texto supera el límite de 20000 caracteres.")
            else:
                self._cancel_requests("Lectura reemplazada.")
                self._latest_id = request["request_id"]
                self._pending = request
        self._signal()
        return request["request_id"]

    def stop(self):
        with self._lock:
            self._latest_id = None
            self._cancel_requests("Lectura detenida.")
        if self._local is not None:
            self._local.stop()
        self._signal()

    def close(self):
        self._closing.set()
        self.stop()

    def _cancel_requests(self, message):
        if self._pending:
            self._pending["cancel"].set()
            self._emit("stopped", self._pending, message)
            self._pending = None
        if self._current:
            self._current["cancel"].set()

    def _signal(self):
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._signal_in_loop)
            except RuntimeError:
                pass

    def _signal_in_loop(self):
        if self._wake is not None:
            self._wake.set()
        if self._active_task is not None and self._current is not None and self._current["cancel"].is_set():
            self._active_task.cancel()

    def _emit(self, event, request, message=None, **extra):
        with self._lock:
            if request["terminal"] or (request["cancel"].is_set() and event != "stopped"):
                return
            if event not in {"error", "stopped"} and request["request_id"] != self._latest_id:
                return
            if event in {"finished", "stopped", "error"}:
                request["terminal"] = True
            payload = {"request_id": request["request_id"], "label": request["label"],
                       "engine": request["settings"]["engine"], **extra}
            if message:
                payload["message"] = str(message)
            self.events.put((event, payload))

    def _ready_event(self):
        with self._lock:
            selected = self._settings["voice_id"]
        self.events.put(("ready", {"voices": [dict(item) for item in EDGE_VOICES] + self._local_voices,
                                   "selected_id": selected, "warning": "", "local_warning": self._local_warning}))

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as exc:
            with self._lock:
                self._failure = str(exc)
            self.events.put(("error", {"request_id": None, "label": "", "message": str(exc), "fatal": True}))

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._local_ready = asyncio.Event()
        self._ready_event()
        try:
            self._cache = SpeechCache(self._cache_dir)
            try:
                self._local = SapiVoiceService(output_path=self._output_path)
            except Exception as exc:
                self._local_warning = str(exc)
                self._local_ready.set()
            while not self._closing.is_set():
                self._drain_local()
                with self._lock:
                    pending, self._pending = self._pending, None
                if pending is not None:
                    await self._cancel_active()
                    with self._lock:
                        self._current = pending
                    self._active_task = asyncio.create_task(self._perform(pending))
                if self._active_task is not None and self._active_task.done():
                    await self._collect_active()
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.025)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._cancel_active()
            if self._local is not None:
                self._local.close()
                while self._local.thread.is_alive():
                    await asyncio.sleep(0.025)
            self._loop = None

    async def _collect_active(self):
        task, self._active_task = self._active_task, None
        try:
            await task
        except asyncio.CancelledError:
            if self._current is not None:
                self._emit("stopped", self._current, "Lectura detenida.")
        with self._lock:
            self._current = None

    async def _cancel_active(self):
        if self._active_task is not None:
            if self._current is not None:
                self._current["cancel"].set()
            self._active_task.cancel()
            await self._collect_active()

    def _check_cancelled(self, request):
        with self._lock:
            if self._closing.is_set() or request["cancel"].is_set() or request["request_id"] != self._latest_id:
                request["cancel"].set()
                raise asyncio.CancelledError

    async def _perform(self, request):
        try:
            self._check_cancelled(request)
            text = prepare_text_for_speech(request["text"])
            if request["settings"]["engine"] == "sapi":
                await self._speak_local(request, text, fallback=False)
            else:
                try:
                    path = await self._edge_audio(request, text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "No se pudo generar voz neuronal (%s); se comprobará el fallback local.",
                        type(exc).__name__, exc_info=True,
                    )
                    self._check_cancelled(request)
                    await self._speak_local(request, text, fallback=True)
                    return
                self._check_cancelled(request)
                player = MCIPlayer()
                try:
                    player.start(path)
                    self._check_cancelled(request)
                    self._emit("started", request)
                    while not player.finished():
                        self._check_cancelled(request)
                        await asyncio.sleep(0.025)
                    self._check_cancelled(request)
                    self._emit("finished", request)
                finally:
                    player.close()
        except asyncio.CancelledError:
            self._emit("stopped", request, "Lectura detenida.")
        except Exception as exc:
            self._emit("error", request, str(exc))

    @staticmethod
    def _edge_parameters(settings):
        speed = settings["rate"]
        if isinstance(speed, str) and speed.endswith("%"):
            rate = speed
        else:
            rate = "-20%" if int(speed) < 0 else "+15%" if int(speed) > 0 else getattr(config, "EDGE_RATE", "-5%")
        base_volume = int(str(getattr(config, "EDGE_VOLUME", "+0%")).removesuffix("%"))
        volume = max(-100, min(100, base_volume + settings["volume"] - 100))
        return rate, f'{volume:+d}%', getattr(config, "EDGE_PITCH", "+0Hz")

    async def _edge_audio(self, request, text):
        settings = request["settings"]
        voice = settings["voice_id"] or getattr(config, "EDGE_VOICE", "es-MX-DaliaNeural")
        if voice not in {item["id"] for item in EDGE_VOICES}:
            raise ValueError("Selecciona una de las voces neuronales españolas disponibles.")
        rate, volume, pitch = self._edge_parameters(settings)
        key = self._cache.key(text, voice, rate, volume, pitch, label=request["label"])
        path = self._cache.get(key)
        if path is not None:
            self._emit("cache_hit", request, "Reproduciendo la voz guardada en caché.")
            return path
        self._emit("generating", request, "Preparando voz neuronal…")
        temporary = self._cache.temporary_path(key)
        try:
            timeout = max(1.0, float(getattr(config, "TTS_NETWORK_TIMEOUT", 20)))
            await asyncio.wait_for(synthesize_edge(text, voice, rate, volume, pitch, temporary), timeout=timeout)
            self._check_cancelled(request)
            return self._cache.store(key, temporary)
        finally:
            self._cache.discard(temporary)

    async def _speak_local(self, request, text, fallback):
        if not self._local_ready.is_set():
            try:
                await asyncio.wait_for(self._local_ready.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
        self._check_cancelled(request)
        spanish = [voice for voice in self._local_voices if voice.get("spanish")]
        selected = None
        if not fallback and request["settings"]["voice_id"]:
            selected = next((voice for voice in self._local_voices if voice["id"] == request["settings"]["voice_id"]), None)
            if selected is None:
                raise RuntimeError("La voz local elegida no está disponible. Selecciona otra voz en Ajustes.")
        elif spanish:
            selected = next((voice for voice in spanish if voice.get("language") in {"es-ES", "es-MX"}), spanish[0])
        if selected is None:
            if fallback:
                raise RuntimeError("Voz neuronal no disponible y no hay una voz local en español instalada. Conecta Internet para generar esta narración; las voces ya guardadas en caché siguen disponibles.")
            raise RuntimeError("No hay una voz local española. Selecciona explícitamente una voz local instalada o utiliza la voz neuronal.")
        if fallback:
            self._emit("fallback", request, "Voz neuronal no disponible. Usando voz local.", engine="sapi")
        rate = request["settings"]["rate"]
        self._local.configure(voice_id=selected["id"], rate=int(rate) if not isinstance(rate, str) else 0,
                              volume=request["settings"]["volume"])
        future = self._loop.create_future()
        local_id = self._local.speak(text, request["label"])
        self._local_waiters[local_id] = (request, future)
        try:
            await future
            self._check_cancelled(request)
        finally:
            self._local_waiters.pop(local_id, None)
            if request["cancel"].is_set() or self._closing.is_set():
                self._local.stop()

    def _drain_local(self):
        if self._local is None:
            return
        while True:
            try:
                event, payload = self._local.events.get_nowait()
            except queue.Empty:
                if not self._local.thread.is_alive():
                    for request, future in list(self._local_waiters.values()):
                        if not future.done():
                            future.set_exception(RuntimeError("El motor de voz local se cerró durante la lectura."))
                return
            if event == "ready":
                self._local_voices = [{**voice, "engine": "sapi"} for voice in payload.get("voices", [])]
                self._local_warning = payload.get("warning", "")
                self._local_ready.set()
                self._ready_event()
            elif payload.get("request_id") in self._local_waiters:
                request, future = self._local_waiters[payload["request_id"]]
                self._emit(event, request, payload.get("message"), engine="sapi")
                if event in {"finished", "stopped", "error"} and not future.done():
                    future.set_result(None)
            elif event == "error" and (payload.get("fatal") or payload.get("request_id") is None):
                self._local_warning = payload.get("message", "La voz local no está disponible.")
                self._local_voices = []
                self._local_ready.set()
                for request, future in list(self._local_waiters.values()):
                    if not future.done():
                        future.set_exception(RuntimeError(self._local_warning))
