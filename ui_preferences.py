"""Preferencias visuales locales, independientes del modelo y de la interfaz.

La aplicación decide la ruta. Los valores inválidos vuelven a su valor inicial
individual; guardar nunca propaga errores de acceso al archivo hacia la GUI.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


THEMES = ("Claro", "Oscuro", "Sistema")
DENSITIES = ("Compacta", "Normal", "Cómoda")
SCALE_RANGE = (0.85, 1.45)
RATIO_RANGES = {"camera": (0.15, 0.85), "workspace": (0.40, 0.95)}
FONT_RANGE = (0.75, 1.75)


def defaults() -> dict[str, Any]:
    """Devuelve un esquema nuevo, incluidos sus diccionarios anidados."""
    return {
        "theme": "Claro",
        "scale": 1.0,
        "density": "Normal",
        "visible": {"top3": True, "history": True, "status": True, "stats": True},
        "collapsed": {"camera": False, "info": False, "top3": False, "history": False},
        "ratios": {"camera": 0.60, "workspace": 0.78},
        "fonts": {"title": 1.0, "confidence": 1.0, "education": 1.0, "history": 1.0},
    }


def _number(value: Any, bounds: tuple[float, float], fallback: float) -> float:
    # bool hereda de int, pero no representa una medida de la interfaz.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return fallback
    if not math.isfinite(number) or not bounds[0] <= number <= bounds[1]:
        return fallback
    return number


def validate(data: Any) -> dict[str, Any]:
    """Normaliza únicamente claves conocidas, sin modificar la entrada."""
    result = defaults()
    if not isinstance(data, dict):
        return result
    for key, options in (("theme", THEMES), ("density", DENSITIES)):
        value = data.get(key)
        if isinstance(value, str) and value in options:
            result[key] = value
    result["scale"] = _number(data.get("scale"), SCALE_RANGE, result["scale"])
    for section in ("visible", "collapsed"):
        values = data.get(section)
        if isinstance(values, dict):
            for key in result[section]:
                if type(values.get(key)) is bool:
                    result[section][key] = values[key]
    for section in ("ratios", "fonts"):
        values = data.get(section)
        if isinstance(values, dict):
            for key, fallback in result[section].items():
                bounds = RATIO_RANGES[key] if section == "ratios" else FONT_RANGE
                result[section][key] = _number(values.get(key), bounds, fallback)
    return result


def load(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Lee JSON UTF-8 (también con BOM); ausencia o corrupción usan defaults."""
    try:
        with Path(path).open("r", encoding="utf-8-sig") as source:
            return validate(json.load(source))
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return defaults()


def save(path: str | os.PathLike[str], prefs: Any) -> bool:
    """Guarda con reemplazo atómico; devuelve False si no pudo persistir.

    El temporal pertenece al mismo directorio para que os.replace sea atómico.
    Una escritura o un reemplazo fallidos conservan el archivo previo.
    """
    temporary: Path | None = None
    try:
        target = Path(path)
        content = json.dumps(validate(prefs), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as destination:
            temporary = Path(destination.name)
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
        return True
    except (OSError, ValueError, TypeError, UnicodeError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # La limpieza tampoco debe impedir que continúe la aplicación.
                pass
