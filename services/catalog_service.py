"""Información educativa independiente del modelo y con recuperación de errores."""

import json
import logging
from pathlib import Path

from config import SCIENTIFIC_NOTICE
from services.labels import clean_label, normalize_label


class Catalog:
    def __init__(self, entries=None, warning=None):
        self.entries = entries or {}
        self.warning = warning

    @classmethod
    def load(cls, path):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("La raíz del catálogo debe ser un objeto JSON.")
            entries = {}
            for label, information in raw.items():
                if not isinstance(information, dict) or not all(isinstance(value, str) for value in information.values()):
                    raise ValueError(f"La ficha de {label!r} debe contener campos de texto.")
                key = normalize_label(label)
                if not key or key in entries:
                    raise ValueError(f"La clave {label!r} está vacía o duplicada al normalizarla.")
                entries[key] = dict(information)
            return cls(entries)
        except (OSError, UnicodeError, ValueError) as exc:
            warning = f"No se pudo cargar catalog.json. El reconocimiento sigue disponible sin fichas educativas. Detalle: {exc}"
            logging.getLogger(__name__).warning(warning)
            return cls(warning=warning)

    def get(self, label):
        result = {"nombre": clean_label(label), "categoria": "Sin catalogar", "formula": "",
                  "uso": "", "capacidad": "", "descripcion": "Información educativa no disponible para esta clase.",
                  "precaucion": "", "caracteristicas": "", "recomendacion": "",
                  "usos_generales": "", "explicacion_voz": "", "explicacion_breve": ""}
        result.update(self.entries.get(normalize_label(label), {}))
        if not result["descripcion"]:
            result["descripcion"] = "Información educativa no disponible para esta clase."
        if not result["usos_generales"] and normalize_label(result["categoria"]) == "producto quimico":
            result["usos_generales"] = result["uso"]
        fallback = (f"La clase registrada es {result['nombre']}. "
                    "No hay una explicación educativa disponible para esta clase.")
        if not result["explicacion_voz"]:
            result["explicacion_voz"] = fallback
        if not result["explicacion_breve"]:
            result["explicacion_breve"] = result["explicacion_voz"]
        return result

    def narration(self, label, student=False):
        """Texto preparado para voz; no genera consejos ni altera la clasificación."""
        entry = self.get(label)
        return entry["explicacion_voz" if student else "explicacion_breve"]

    def describe(self, label, student=False):
        """Texto del panel: resumen Normal o ficha Estudiante, sin nombre/confianza."""
        entry = self.get(label)
        chemical = normalize_label(entry["categoria"]) == "producto quimico"
        sections = [("Categoría", entry["categoria"])]
        if chemical:
            sections.extend([("Fórmula", entry["formula"]), ("Descripción", entry["descripcion"])])
            if student:
                sections.append(("Usos generales", entry["usos_generales"]))
            sections.append(("Precaución", entry["precaucion"]))
        else:
            sections.append(("Capacidad", entry["capacidad"]))
            if student:
                sections.append(("Descripción", entry["descripcion"]))
            sections.append(("¿Para qué sirve?", entry["uso"]))
            if student:
                sections.extend([("Características", entry["caracteristicas"]),
                                 ("Recomendación", entry["recomendacion"])])
            elif not entry["uso"]:
                sections.append(("Descripción", entry["descripcion"]))
        text = "\n\n".join(f"{title}\n{value}" for title, value in sections if value)
        if chemical:
            text += "\n\n" + SCIENTIFIC_NOTICE
        return text
