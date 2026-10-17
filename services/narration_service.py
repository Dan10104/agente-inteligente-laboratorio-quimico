"""Evita repeticiones de voz sin depender de Tk, del modelo ni del motor SAPI.

La interfaz entrega una observación por cada resultado nuevo de la cámara,
después de aplicar su estabilización existente. Las consultas del bucle de UI
no aportan evidencia temporal. Los botones manuales no necesitan estabilidad.
Solo el evento real de inicio de voz confirma la narración.
"""

import math
import time

from services.labels import clean_label, normalize_label


class NarrationGate:
    """Estado de narraciones iniciadas y una sola solicitud pendiente.

    Se utiliza desde el hilo que procesa los eventos de la interfaz. El motor
    informa el inicio/error/parada mediante sus eventos; no debe tocar este
    objeto desde otro hilo. ``started`` libera la reserva: quien integra el
    servicio también controla si el motor sigue hablando antes de iniciar una
    explicación automática nueva.
    """

    def __init__(self, cooldown_seconds=2, clock=time.monotonic, *,
                 stable_seconds=0.7, absence_seconds=1.0):
        try:
            cooldown = float(cooldown_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("El intervalo de voz debe ser un número no negativo.") from exc
        if not math.isfinite(cooldown) or cooldown < 0:
            raise ValueError("El intervalo de voz debe ser un número finito no negativo.")
        if not callable(clock):
            raise TypeError("El reloj de narración debe ser una función monotónica.")
        for name, value in (("estabilidad", stable_seconds), ("ausencia", absence_seconds)):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"El intervalo de {name} debe ser un número finito positivo.")
        self.cooldown_seconds = cooldown
        self.stable_seconds = float(stable_seconds)
        self.absence_seconds = float(absence_seconds)
        self.clock = clock
        self.last_spoken_class = None
        self._last_spoken_key = None
        self._last_spoken_times = {}
        self._reservation = None
        self._candidate_key = None
        self._candidate_since = None
        self._stable_key = None
        self._absent_since = None
        self._absence_rearmed = False

    def reset(self):
        """Nueva fuente/evidencia: libera el episodio, conservando cooldowns."""
        self.last_spoken_class = None
        self._last_spoken_key = None
        self._reservation = None
        self._candidate_key = None
        self._candidate_since = None
        self._stable_key = None
        self._absent_since = None
        self._absence_rearmed = False

    def observe(self, label):
        """Recibe SOLO resultados nuevos, no ticks; None indica ausencia válida.

        Devuelve True una vez cuando la ausencia sostenida permite narrar otra
        vez el mismo objeto. Un resultado aislado no libera el bloqueo.
        """
        now = self.clock()
        key = normalize_label(label) if label else None
        if key:
            self._absent_since = None
            self._absence_rearmed = False
            if key != self._candidate_key:
                self._candidate_key = key
                self._candidate_since = now
                self._stable_key = None
            elif now - self._candidate_since >= self.stable_seconds:
                self._stable_key = key
            return False
        self._candidate_key = None
        self._candidate_since = None
        self._stable_key = None
        if self._absent_since is None:
            self._absent_since = now
        elif not self._absence_rearmed and now - self._absent_since >= self.absence_seconds:
            self.last_spoken_class = None
            self._last_spoken_key = None
            # Un inicio tardío de la solicitud anterior no bloquea el episodio nuevo.
            self._reservation = None
            self._absence_rearmed = True
            return True
        return False

    def is_stable(self, label):
        """Consulta sin avanzar tiempos ni reutilizar un único fotograma."""
        key = normalize_label(label) if label else None
        return bool(key and key == self._stable_key)

    def is_absent(self):
        """La ausencia ya fue confirmada por resultados nuevos de cámara."""
        return self._absence_rearmed

    def can_auto(self, label):
        """Permite una clase distinta cuyo último inicio cumplió el cooldown."""
        key = normalize_label(label)
        if not key or self._reservation is not None or key == self._last_spoken_key:
            return False
        previous_start = self._last_spoken_times.get(key)
        return previous_start is None or self.clock() - previous_start >= self.cooldown_seconds

    def reserve(self, label, request_id):
        """Reserva sin marcar como hablado; también sirve para repetición manual.

        Devuelve False si existe cualquier reserva pendiente. Los identificadores
        se generan una sola vez por solicitud y no se reutilizan.
        """
        if request_id is None:
            raise ValueError("La solicitud de voz necesita un identificador único.")
        name = clean_label(label)
        key = normalize_label(label)
        if not key:
            raise ValueError("No se puede narrar una clase sin nombre.")
        if self._reservation is not None:
            return False
        self._reservation = (request_id, name, key)
        return True

    def started(self, request_id):
        """Confirma únicamente el inicio de la solicitud todavía reservada."""
        if self._reservation is None or self._reservation[0] != request_id:
            return False
        _, name, key = self._reservation
        self._last_spoken_times[key] = self.clock()
        # REPETIR durante una ausencia no pertenece al próximo objeto detectado.
        self.last_spoken_class = None if self._absence_rearmed else name
        self._last_spoken_key = None if self._absence_rearmed else key
        self._reservation = None
        return True

    def cancel(self, request_id=None):
        """Libera una reserva sin olvidar voces iniciadas ni reiniciar cooldowns.

        Sin identificador cancela la reserva actual. Un evento viejo con un
        identificador distinto no puede cancelar una solicitud más reciente.
        """
        if self._reservation is None:
            return False
        if request_id is not None and self._reservation[0] != request_id:
            return False
        self._reservation = None
        return True
