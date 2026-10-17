"""Decisiones transparentes a partir de los vectores reales de la red neuronal."""

from collections import deque
from dataclasses import dataclass

import numpy as np

from services.labels import normalize_label


RECOGNIZED = "RECONOCIDO"
ANALYZING = "ANALIZANDO"
UNCERTAIN = "IDENTIFICACIÓN INSEGURA"
UNKNOWN = "OBJETO NO REGISTRADO"
_OTHER_LABELS = {"otro", "no reconocido", "otro / no reconocido"}


def is_other_label(label: str) -> bool:
    return normalize_label(label) in _OTHER_LABELS


@dataclass(frozen=True)
class Decision:
    status: str
    index: int
    confidence: float
    margin: float
    agreement: float
    probabilities: np.ndarray
    frames: int


class TemporalDecision:
    def __init__(self, labels, confidence_threshold=0.80, min_margin=0.12,
                 smoothing_window=12, stability_ratio=0.70):
        self.labels = tuple(labels)
        if not self.labels:
            raise ValueError("No se puede estabilizar una predicción sin etiquetas.")
        if not 0 <= confidence_threshold <= 1 or not 0 <= min_margin <= 1:
            raise ValueError("La confianza y el margen deben estar entre 0 y 1.")
        if not 0 < stability_ratio <= 1:
            raise ValueError("La proporción de estabilidad debe ser mayor que 0 y como máximo 1.")
        if isinstance(smoothing_window, bool) or int(smoothing_window) != smoothing_window or smoothing_window < 1:
            raise ValueError("La ventana de suavizado debe ser un número entero positivo.")
        self.confidence_threshold = float(confidence_threshold)
        self.min_margin = float(min_margin)
        self.smoothing_window = int(smoothing_window)
        self.stability_ratio = float(stability_ratio)
        self._predictions = deque(maxlen=self.smoothing_window)

    def reset(self):
        """Vacía la evidencia al cambiar de fuente, cámara, ROI o ajustes."""
        self._predictions.clear()

    def _validate(self, probabilities):
        vector = np.asarray(probabilities, dtype=np.float64)
        if vector.shape != (len(self.labels),):
            raise ValueError(f"Se esperaba un vector de {len(self.labels)} probabilidades, se recibió {vector.shape}.")
        if not np.all(np.isfinite(vector)):
            raise ValueError("El modelo devolvió probabilidades no finitas.")
        if np.any(vector < 0) or np.any(vector > 1) or not np.isclose(vector.sum(), 1, atol=1e-3):
            raise ValueError("El modelo debe devolver probabilidades entre 0 y 1 cuya suma sea aproximadamente 1.")
        return vector.copy()

    def _decision(self, vector, frames, agreement, warmed_up):
        ranking = np.argsort(-vector, kind="stable")
        index = int(ranking[0])
        confidence = float(vector[index])
        second = float(vector[ranking[1]]) if len(ranking) > 1 else 0.0
        margin = confidence - second
        # Esta tolerancia solo compara límites; nunca cambia los porcentajes.
        if confidence + 1e-9 < self.confidence_threshold or margin + 1e-9 < self.min_margin:
            status = UNCERTAIN
        elif not warmed_up or agreement + 1e-9 < self.stability_ratio:
            status = ANALYZING
        elif is_other_label(self.labels[index]):
            status = UNKNOWN
        else:
            status = RECOGNIZED
        result = vector.copy()
        result.setflags(write=False)
        return Decision(status, index, confidence, margin, agreement, result, frames)

    def update(self, probabilities):
        self._predictions.append(self._validate(probabilities))
        vectors = np.stack(self._predictions)
        average = vectors.mean(axis=0)
        candidate = int(np.argmax(average))
        agreement = float(np.mean(np.argmax(vectors, axis=1) == candidate))
        return self._decision(average, len(vectors), agreement, len(vectors) == self.smoothing_window)

    def evaluate_still(self, probabilities):
        """Una imagen: una inferencia, sin simular evidencia temporal ni tocarla."""
        return self._decision(self._validate(probabilities), 1, 1.0, True)
