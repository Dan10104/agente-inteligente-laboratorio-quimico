"""Etiquetas dinámicas del modelo y claves tolerantes para el catálogo."""

from pathlib import Path
import re
import unicodedata


_INDEX_PREFIX = re.compile(r"^\s*(\d+)\s+")


def clean_label(label: str) -> str:
    """Quita solo el índice inicial y normaliza espacios para presentar el nombre."""
    label = str(label).lstrip("\ufeff")
    return " ".join(_INDEX_PREFIX.sub("", label, count=1).split())


def normalize_label(label: str) -> str:
    """Conserva la capacidad: los matraces de 100 y 250 mL nunca se fusionan."""
    text = clean_label(label).casefold()
    text = "".join(character for character in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(character))
    text = re.sub(r"(?<=\d)\s*(?=ml\b)", " ", text)
    text = re.sub(r"\s*/\s*", " / ", text)
    return " ".join(text.split())


def load_labels(path: str | Path) -> list[str]:
    """Lee el orden real de labels; rechaza índices ambiguos o nombres repetidos."""
    path = Path(path)
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    except OSError as exc:
        raise ValueError(f"No se pudo leer el archivo de etiquetas: {path}. {exc}") from exc
    except UnicodeError as exc:
        raise ValueError(f"El archivo de etiquetas debe estar codificado en UTF-8: {path}.") from exc
    if not lines:
        raise ValueError(f"El archivo de etiquetas está vacío: {path}.")
    prefixes = [_INDEX_PREFIX.match(line) for line in lines]
    if any(prefixes):
        if not all(prefixes):
            raise ValueError("Las etiquetas mezclan líneas con y sin índice. Todas deben seguir el mismo formato.")
        indices = [int(prefix.group(1)) for prefix in prefixes]
        if indices != list(range(len(lines))):
            raise ValueError("Los índices de labels.txt deben ser consecutivos desde 0 y coincidir con el orden de sus líneas.")
    labels = [clean_label(line) for line in lines]
    if any(not label or label.isdigit() for label in labels):
        raise ValueError("Hay una etiqueta vacía o sin nombre después de su índice en labels.txt.")
    keys = [normalize_label(label) for label in labels]
    if len(set(keys)) != len(keys):
        raise ValueError("labels.txt contiene nombres de clase duplicados después de normalizar espacios y acentos.")
    return labels
