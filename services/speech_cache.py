"""Caché local de audio neuronal con claves completas y publicación atómica.

La validación comprueba estructura MP3, no inteligibilidad ni pronunciación.
El encabezado de cada frame MPEG Layer III ocupa cuatro bytes:
https://www.rfc-editor.org/rfc/rfc5219.html#section-3
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from services.speech_text import prepare_text_for_speech


_KEY = re.compile(r"[a-z0-9_]{1,64}-[0-9a-f]{64}\Z")
_MIN_MP3_BYTES = 256


def _frame_header(header):
    if len(header) != 4:
        return None
    bits = int.from_bytes(header, "big")
    version = (bits >> 19) & 3
    layer = (bits >> 17) & 3
    bitrate_index = (bits >> 12) & 15
    sample_index = (bits >> 10) & 3
    if bits >> 21 != 0x7FF or version == 1 or layer != 1:
        return None
    if bitrate_index in (0, 15) or sample_index == 3 or bits & 3 == 2:
        return None
    rates = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if version == 3 else (
        8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    divisor = 1 if version == 3 else 2 if version == 2 else 4
    sample_rate = (44100, 48000, 32000)[sample_index] // divisor
    frame_length = (144000 if version == 3 else 72000) * rates[bitrate_index - 1] // sample_rate
    frame_length += (bits >> 9) & 1
    return frame_length, version, sample_rate


def is_valid_mp3(path):
    """Rechaza archivos vacíos, errores HTTP y audio evidentemente truncado.

    Exige dos frames Layer III contiguos y completos tras una etiqueta ID3
    opcional. Es una comprobación estructural mínima, no un decodificador.
    """
    try:
        path = Path(path)
        size = path.stat().st_size
        if size < _MIN_MP3_BYTES or not path.is_file():
            return False
        with path.open("rb") as audio:
            first = audio.read(10)
            offset = 0
            if first[:3] == b"ID3":
                if len(first) < 10 or first[3] not in (2, 3, 4) or any(value & 0x80 for value in first[6:10]):
                    return False
                tag_size = sum(value << shift for value, shift in zip(first[6:10], (21, 14, 7, 0)))
                offset = 10 + tag_size + (10 if first[3] == 4 and first[5] & 0x10 else 0)
            if offset + 8 > size:
                return False
            audio.seek(offset)
            candidate = _frame_header(audio.read(4))
            if candidate is None:
                return False
            frame_length, version, sample_rate = candidate
            second_offset = offset + frame_length
            if second_offset + 4 > size:
                return False
            audio.seek(second_offset)
            following = _frame_header(audio.read(4))
            if following is None:
                return False
            return following[1:] == (version, sample_rate) and second_offset + following[0] <= size
    except (OSError, ValueError, TypeError):
        return False


def _safe_class_name(label):
    decomposed = unicodedata.normalize("NFKD", str(label))
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    safe = re.sub(r"[^a-z0-9]+", "_", plain.casefold()).strip("_")
    return (safe or "narracion")[:64].rstrip("_")


class SpeechCache:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, text, voice, rate, volume, pitch, *, label="narracion"):
        normalized = prepare_text_for_speech(text)
        if not normalized:
            raise ValueError("No se puede crear una clave de audio para un texto vacío.")
        payload = {"version": 1, "text": normalized, "voice": str(voice).strip(),
                   "rate": str(rate).strip(), "volume": str(volume).strip(), "pitch": str(pitch).strip()}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return _safe_class_name(label) + "-" + hashlib.sha256(encoded).hexdigest()

    def _validated_key(self, key):
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            raise ValueError("La clave de caché de voz no es válida.")
        return key

    def _target(self, key):
        return self.root / (self._validated_key(key) + ".mp3")

    def get(self, key):
        target = self._target(key)
        # Nunca devolver enlaces hacia archivos ajenos a esta caché.
        if target.is_symlink() or target.resolve().parent != self.root:
            return None
        return target if is_valid_mp3(target) else None

    def temporary_path(self, key):
        """Reserva un archivo exclusivo en el mismo volumen que el definitivo."""
        key = self._validated_key(key)
        descriptor, name = tempfile.mkstemp(prefix="." + key + ".", suffix=".part.mp3", dir=self.root)
        os.close(descriptor)
        return Path(name)

    def _temporary(self, path, key=None):
        path = Path(path)
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("El archivo temporal de voz debe pertenecer a esta caché.")
        if not path.name.startswith(".") or not path.name.endswith(".part.mp3"):
            raise ValueError("Solo se pueden limpiar archivos temporales de voz.")
        if key is not None and not path.name.startswith("." + self._validated_key(key) + "."):
            raise ValueError("El archivo temporal corresponde a otra clave de voz.")
        return path

    def discard(self, temporary):
        self._temporary(temporary).unlink(missing_ok=True)

    def store(self, key, temporary):
        """Publica audio terminado; no deja un nombre definitivo a medio escribir."""
        target = self._target(key)
        temporary = self._temporary(temporary, key)
        try:
            if not is_valid_mp3(temporary):
                raise ValueError("El motor no generó un archivo MP3 válido y completo.")
            existing = self.get(key)
            if existing is not None:
                return existing
            with temporary.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
