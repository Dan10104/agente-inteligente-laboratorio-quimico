"""Ajustes de inferencia y rutas locales; no son parámetros de entrenamiento."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_TITLE = "AGENTE INTELIGENTE DE LABORATORIO QUÍMICO"
CONFIDENCE_THRESHOLD = 0.80
MIN_MARGIN = 0.12
SMOOTHING_WINDOW = 12
STABILITY_RATIO = 0.70
LOG_COOLDOWN_SECONDS = 5
DEFAULT_CAMERA_INDEX = 0
USE_CENTER_ROI = True
ROI_FRACTION = 0.85
CAMERA_INDICES = (0, 1, 2, 3)
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_RETRY_SECONDS = 2
CAMERA_MAX_RETRIES = 3
INFERENCE_INTERVAL_MS = 100
RENDER_INTERVAL_MS = 33
HISTORY_VISIBLE_ROWS = 50
VOICE_COOLDOWN_SECONDS = 2
VOICE_STABLE_SECONDS = 0.7
VOICE_ABSENCE_SECONDS = 1.0
VOICE_RATE = 0
VOICE_VOLUME = 100
TTS_ENGINE = "edge"
EDGE_VOICE = "es-MX-DaliaNeural"
EDGE_RATE = "-5%"
EDGE_VOLUME = "+0%"
EDGE_PITCH = "+0Hz"
TTS_CACHE_DIR = BASE_DIR / "cache" / "tts"
TTS_NETWORK_TIMEOUT = 20
MODEL_PATH = BASE_DIR / "keras_model.h5"
LABELS_PATH = BASE_DIR / "labels.txt"
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
HISTORY_PATH = BASE_DIR / "data" / "historial_detecciones.csv"
CAPTURES_DIR = BASE_DIR / "captures"
LOG_PATH = BASE_DIR / "logs" / "agente.log"
SCIENTIFIC_NOTICE = (
    "Reconocimiento visual del producto registrado. No sustituye la identificación "
    "química ni la ficha de datos de seguridad."
)
