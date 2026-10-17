"""Preparación del texto enviado a TTS, sin modificar las fichas del catálogo."""
import re
import unicodedata


_CHEMICALS = {
    "NaOH": "hidróxido de sodio",
    "KMnO4": "permanganato de potasio",
    "AgNO3": "nitrato de plata",
    "C3H6O": "acetona",
}
_FORMULAS = "(?:" + "|".join(re.escape(item) for item in _CHEMICALS) + ")"
_FORMULA_WORD = re.compile(r"(?<!\w)(" + _FORMULAS + r")(?!\w)")
_RELATIVE_FORMULA = re.compile(
    r",\s*(?i:cuya\s+f[oó]rmula(?:\s+molecular)?\s+es)\s+(" + _FORMULAS + r")(?!\w)"
)
_FORMULA_SENTENCE = re.compile(
    r"(?P<boundary>^|(?<=[.!?])\s+)(?i:(?:Su|La)\s+f[oó]rmula(?:\s+molecular)?\s+es)\s+"
    r"(?P<formula>" + _FORMULAS + r")(?!\w)(?P<end>[.!?]|$)"
)
_FORMULA_PHRASE = re.compile(
    r"(?<!\w)(?i:(?:Su|La)\s+f[oó]rmula(?:\s+molecular)?\s+es)\s+(" + _FORMULAS + r")(?!\w)"
)
_VOLUMES = {"100": "cien", "250": "doscientos cincuenta", "50": "cincuenta"}
_VOLUME = re.compile(r"(?<![\w.,])(?P<volume>100|250|50)\s*(?:mL|mililitros)(?!\w)", re.IGNORECASE)


def _plain(text):
    return "".join(character for character in unicodedata.normalize("NFD", text.casefold())
                   if not unicodedata.combining(character))


def _recently_named(before, name):
    # Limitar el contexto a una oración evita omitir información de otro elemento.
    before = before.rstrip().rstrip(".!?")
    sentence = re.split(r"[.!?]", before)[-1]
    return re.search(r"(?<!\w)" + re.escape(_plain(name)) + r"(?!\w)", _plain(sentence)) is not None


def prepare_text_for_speech(text):
    """Expande capacidades y nombres químicos con límites de palabra.

    No cambia una frase correcta en «su fórmula es acetona»: las cláusulas de
    fórmula se omiten si repiten el nombre inmediato o se reformulan como nombre
    de la sustancia. La fórmula escrita en la interfaz permanece intacta.
    """
    if not isinstance(text, str):
        raise TypeError("El texto para narrar debe ser una cadena.")
    text = " ".join(unicodedata.normalize("NFC", text).split())

    def relative(match):
        name = _CHEMICALS[match.group(1)]
        if _recently_named(text[:match.start()], name):
            return ""
        return ", identificado como " + name

    text = _RELATIVE_FORMULA.sub(relative, text)

    def sentence(match):
        name = _CHEMICALS[match.group("formula")]
        if _recently_named(text[:match.start()], name):
            return ""
        return match.group("boundary") + "La sustancia registrada es " + name + match.group("end")

    text = _FORMULA_SENTENCE.sub(sentence, text)
    text = _FORMULA_PHRASE.sub(lambda match: "la sustancia registrada es " + _CHEMICALS[match.group(1)], text)
    text = _FORMULA_WORD.sub(lambda match: _CHEMICALS[match.group(1)], text)
    text = _VOLUME.sub(lambda match: _VOLUMES[match.group("volume")] + " mililitros", text)
    return " ".join(text.split())
