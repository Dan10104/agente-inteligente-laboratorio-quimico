"""Carga única del modelo original y preprocesamiento Teachable Machine."""
import os
import threading
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

import numpy as np
from PIL import Image, ImageOps

import config
from services.labels import load_labels


def roi_bounds(width, height, fraction=config.ROI_FRACTION):
    """Cuadrado grande centrado, idéntico para la guía y la inferencia."""
    side = max(1, round(min(width, height) * fraction))
    x, y = (width - side) // 2, (height - side) // 2
    return x, y, x + side, y + side


class ModelService:
    def __init__(self, model_path=config.MODEL_PATH, labels_path=config.LABELS_PATH):
        self.model_path, self.labels_path = Path(model_path), Path(labels_path)
        self.model = None
        self.labels = []
        self.lock = threading.Lock()

    def load(self):
        if self.model is not None:
            return self
        if not self.model_path.is_file():
            raise FileNotFoundError(f"No se encontró el modelo: {self.model_path}")
        self.labels = load_labels(self.labels_path)
        import tensorflow as tf
        # Limitar CPU evita monopolizar el equipo durante una presentación.
        try:
            tf.config.threading.set_intra_op_parallelism_threads(2)
            tf.config.threading.set_inter_op_parallelism_threads(2)
        except RuntimeError:
            pass
        model = tf.keras.models.load_model(str(self.model_path), compile=False)
        shape, output = model.input_shape, model.output_shape
        if isinstance(shape, list) or len(shape) != 4 or shape[-1] != 3:
            raise ValueError(f"Se esperaba una entrada RGB NHWC; entrada real: {shape}")
        if shape[0] not in (None, 1) or not all(isinstance(v, int) and v > 0 for v in shape[1:3]):
            raise ValueError(f"Dimensiones de entrada incompatibles: {shape}")
        if isinstance(output, list) or len(output) != 2:
            raise ValueError(f"Se esperaba clasificación de una sola salida; salida: {output}")
        if output[-1] != len(self.labels):
            raise ValueError(f"Etiquetas y modelo no coinciden: {len(self.labels)} etiquetas, "
                             f"{output[-1]} salidas. Revise el par labels.txt / keras_model.h5.")
        self.input_shape, self.output_shape = tuple(shape), tuple(output)
        self.image_size = (shape[2], shape[1])
        self.model = model
        return self

    def preprocess(self, rgb, use_roi=False):
        if self.model is None:
            raise RuntimeError("El modelo todavía no está cargado.")
        image = rgb if isinstance(rgb, Image.Image) else Image.fromarray(rgb)
        image = ImageOps.exif_transpose(image).convert("RGB")
        if use_roi:
            image = image.crop(roi_bounds(*image.size))
        image = ImageOps.fit(image, self.image_size, Image.Resampling.LANCZOS,
                             centering=(0.5, 0.5))
        array = np.asarray(image, dtype=np.float32)
        return np.expand_dims(array / np.float32(127.5) - np.float32(1.0), axis=0)

    def predict_rgb(self, rgb, use_roi=False):
        batch = self.preprocess(rgb, use_roi)
        with self.lock:
            result = np.asarray(self.model.predict(batch, verbose=0), dtype=np.float32)
        if result.shape != (1, len(self.labels)):
            raise ValueError(f"Salida inesperada de la red: {result.shape}")
        probabilities = result[0]
        if (not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0)
                or np.any(probabilities > 1.00001)
                or not np.isclose(probabilities.sum(), 1.0, atol=0.01)):
            raise ValueError("El modelo no devolvió un vector válido de probabilidades.")
        return probabilities

    def predict_bgr(self, frame, use_roi=False):
        import cv2
        return self.predict_rgb(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), use_roi)

    def predict_file(self, path, use_roi=False):
        with Image.open(path) as source:
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            return rgb.copy(), self.predict_rgb(rgb, use_roi)

    def top_n(self, probabilities, n=3):
        return [(int(i), self.labels[i], float(probabilities[i]))
                for i in np.argsort(-np.asarray(probabilities), kind="stable")[:n]]
