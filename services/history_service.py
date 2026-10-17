"""Persistencia CSV con debounce y estadísticas exclusivamente de la sesión."""

import csv
from datetime import datetime
import logging
import math
from pathlib import Path
import re
import time

from services.decision_service import is_other_label
from services.labels import clean_label, normalize_label


CSV_FIELDS = ("fecha", "hora", "clase", "confianza", "categoria")
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def sanitize_capture_label(label, max_length=64):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean_label(label))
    name = re.sub(r"\s+", "_", name).strip(" ._")[:max_length].rstrip(" .")
    if not name:
        name = "sin_identificar"
    if name.split(".")[0].casefold() in _WINDOWS_DEVICES:
        name = "objeto_" + name
    return name


def capture_filename(label, confidence, when=None):
    when = when or datetime.now()
    if not math.isfinite(float(confidence)) or not 0 <= confidence <= 1:
        raise ValueError("La confianza de la captura debe estar entre 0 y 1.")
    return f"{when:%Y-%m-%d_%H-%M-%S-%f}_{sanitize_capture_label(label)}_{confidence * 100:.1f}.jpg"


class HistoryService:
    def __init__(self, path, cooldown_seconds=5, max_recent=100):
        self.path = Path(path)
        if cooldown_seconds < 0 or not math.isfinite(float(cooldown_seconds)):
            raise ValueError("El intervalo del historial debe ser un número no negativo.")
        if max_recent < 1:
            raise ValueError("El historial visible debe conservar al menos un registro.")
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_recent = int(max_recent)
        self.records = []
        self._last_times = {}
        self._last_class = None
        self.session_count = 0
        self._confidence_total = 0.0
        self._materials = set()
        self._products = set()
        self.warning = None
        self.persistence_enabled = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size:
                with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    if reader.fieldnames != list(CSV_FIELDS):
                        raise ValueError("El historial CSV tiene columnas incompatibles. Se esperaba: " + ",".join(CSV_FIELDS))
                    for row in reader:
                        if any(row.get(field) is None for field in CSV_FIELDS) or None in row:
                            self.warning = "Se omitió una fila incompleta del historial CSV."
                            logging.getLogger(__name__).warning(self.warning)
                            continue
                        self.records.insert(0, row)
                        del self.records[self.max_recent:]
            else:
                with self.path.open("w", encoding="utf-8-sig", newline="") as handle:
                    csv.DictWriter(handle, fieldnames=CSV_FIELDS).writeheader()
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            self.persistence_enabled = False
            self.warning = f"No se pudo abrir el historial CSV; esta sesión usa memoria y no se guardará. El archivo original se conserva. Detalle: {exc}"
            logging.getLogger(__name__).warning(self.warning)

    @property
    def mean_confidence(self):
        return self._confidence_total / self.session_count if self.session_count else 0.0

    @property
    def material_count(self):
        return len(self._materials)

    @property
    def product_count(self):
        return len(self._products)

    @property
    def stats(self):
        return {"detections": self.session_count, "mean_confidence": self.mean_confidence,
                "materials": self.material_count, "products": self.product_count}

    def recent(self, limit=30):
        return [dict(row) for row in self.records[:max(0, limit)]]

    def record(self, label, confidence, category, *, timestamp=None, monotonic_time=None,
               confirmed=False):
        """Una confirmación explícita omite el debounce entre detecciones automáticas."""
        if is_other_label(label):
            return None
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("La confianza del historial debe estar entre 0 y 1.")
        name = clean_label(label)
        if not name:
            raise ValueError("No se puede registrar una clase sin nombre.")
        key = normalize_label(name)
        elapsed_now = time.monotonic() if monotonic_time is None else float(monotonic_time)
        last_time = self._last_times.get(key)
        if not confirmed and key == self._last_class and last_time is not None and elapsed_now - last_time < self.cooldown_seconds:
            return None
        timestamp = timestamp or datetime.now()
        record = {"fecha": timestamp.strftime("%Y-%m-%d"), "hora": timestamp.strftime("%H:%M:%S"),
                  "clase": name, "confianza": f"{confidence * 100:.1f}", "categoria": str(category)}
        # Actualizar estado únicamente tras guardar satisfactoriamente.
        if self.persistence_enabled:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=CSV_FIELDS).writerow(record)
        self._last_times[key] = elapsed_now
        self._last_class = key
        self.records.insert(0, record)
        del self.records[self.max_recent:]
        self.session_count += 1
        self._confidence_total += confidence
        normalized_category = normalize_label(category)
        if normalized_category.startswith("material"):
            self._materials.add(key)
        elif normalized_category == "producto quimico":
            self._products.add(key)
        return dict(record)
