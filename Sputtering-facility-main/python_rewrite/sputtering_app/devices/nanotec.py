from __future__ import annotations

"""
Nanotec-SMCI Treiber fuer den Python-Rewrite.

Dieses Modul ist sicherheitsnah, weil es direkt Fahrbefehle an die Schrittmotor-
steuerung sendet. Deshalb gilt hier bewusst ein strenger Stil:

1) Eingaben werden vor dem Senden validiert.
2) Kritische Sollwerte werden in nachvollziehbare Grenzen gebracht.
3) Statuscodes werden klar in lesbaren Text uebersetzt.
4) Das Legacy-Befehlsmuster aus dem C++-Original bleibt erhalten, damit sich
   das reale Anlagenverhalten nicht ungewollt veraendert.

Wichtige Randnotiz:
- Das C++-Original nutzt fuer die Profilfahrt `p1` (relative Position) und kodiert
  die Richtung ueber das Vorzeichen der Schrittzahl (`s<signed>`).
- Auch wenn manche Dokumentationen zusaetzlich `d0/d1` nutzen, behalten wir dieses
  Legacy-Muster absichtlich bei, weil es in der bestehenden Anlage bereits genutzt
  wurde.
"""

import math
import re
import time
from dataclasses import dataclass

from .. import protocols
from ..models import MotorDirection, MotorState
from .transport import SerialDeviceTransport, SerialSettings


# Laufstatuscodes aus dem Legacy-C++-Teil:
# - 16 / 160: Motor ist in Fahrt.
# - 17 / 161: Steuerung bereit, kein Lauf aktiv.
# - 163: Referenzfahrt abgeschlossen ("referenziert").
_STATUS_RUNNING = {16, 160}
_STATUS_READY = {17, 161, 163}


class NanotecValidationError(ValueError):
    """Fachliche Validierungsfehler fuer Motor-Sollwerte."""


@dataclass(frozen=True)
class NormalizedMotorCommand:
    """
    Normiertes Parameterbild fuer einen Nanotec-Fahrdatensatz.

    Warum dieses Objekt?
    - Wir validieren Werte genau einmal.
    - Danach arbeitet der restliche Treiber mit bereits sicheren, normalisierten
      Zahlenwerten.
    """

    target_speed: int
    target_position_mm: float
    step_mode_to_set: int
    loops: int


@dataclass
class NanotecDevice:
    transport: SerialDeviceTransport
    settings: SerialSettings

    # Erlaubte Schrittmodi (wie im Legacy-UI hinterlegt).
    ALLOWED_STEP_MODES: tuple[int, ...] = (1, 2, 4, 5, 8, 10, 16, 32, 64, 254, 255)

    # Sichere Betriebsgrenzen aus dem Legacy-Dialog:
    # - target speed wurde dort auf 1..1100 begrenzt.
    # - loops wurde auf 1..254 begrenzt (0 waere Endlosschleife und fuer GUI-
    #   Bedienung ohne zusaetzliche Schutzlogik riskant).
    MIN_SPEED: int = 1
    MAX_SPEED: int = 1100
    MIN_LOOPS: int = 1
    MAX_LOOPS: int = 254

    # Positionsgrenze als Plausibilitaets-Schutz gegen Tippfehler.
    # Diese Grenze ist bewusst grosszuegig, damit reale Fahrwege der Anlage nicht
    # kuenstlich eingeschraenkt werden. Sie soll vor allem Eingabefehler wie
    # "600000000" abfangen.
    MAX_ABS_TARGET_MM: float = 100000.0

    def check_motor(self, address: str) -> bool:
        """
        Prueft, ob an der Adresse eine plausibel antwortende Nanotec-Steuerung sitzt.

        Methode:
        - `#<addr>v` lesen
        - Antwort auf typische Identifikatoren pruefen.
        """

        raw = self._query(address, "v")
        text = raw.decode("latin1", errors="ignore").strip().upper()
        if len(text) < 2:
            return False

        # Robust gegen unterschiedliche Firmware-Texte:
        # - "SMCI47-S ..."
        # - "SMCI..."
        # - "NANOTEC ..."
        return ("SMCI47" in text) or ("SMCI" in text) or ("NANOTEC" in text)

    def configure_motor(self, motor: MotorState) -> None:
        """
        Schreibt einen sicheren Grunddatensatz auf das Motorprofil.

        Ablauf in Legacy-Reihenfolge:
        1) Stop (`S`)
        2) Profilmodus relativ (`p1`)
        3) Schrittmodus (`g...` + Ruecklese `Zg`)
        4) Geschwindigkeit (`o...`)
        5) Weg (`s...`)
        6) Wiederholungen (`W...`)
        7) Starttrigger auf Eingang 1 (`t1`)
        8) Position ruecklesen (`C`)
        """

        normalized = self._normalize_motor_command(motor)
        self._apply_normalized_values_to_state(motor, normalized)

        self._write(motor.address, "S")
        self._write(motor.address, "p1")

        # Schrittmodus aktiv setzen und ruecklesen.
        self._apply_step_mode_if_needed(motor, force=True)

        self._write(motor.address, f"o{int(motor.target_speed)}")
        target_steps = self._target_steps(motor, use_active_step_mode=True)
        self._write(motor.address, f"s{target_steps}")
        self._write(motor.address, f"W{int(motor.loops)}")
        self._write(motor.address, "t1")

        pos_raw = self._query(motor.address, "C")
        pos = self._parse_prefixed_int(pos_raw, "C")
        if pos is not None:
            motor.initial_position_steps = pos
            motor.actual_position_mm = self._steps_to_mm(pos, motor)

        motor.expected_runtime_sec = self._expected_runtime(motor, target_steps)
        motor.runtime_sec = 0.0
        motor.rest_sec = motor.expected_runtime_sec
        motor.status_text = "Controller ready"

    def start_profile(self, motor: MotorState, *, now_s: float | None = None) -> None:
        """
        Startet eine Profilfahrt mit zuvor validierten Parametern.

        Sicherheitsaspekt:
        - Alle Sollwerte werden vor dem ersten Write geprueft.
        - Falls der Benutzer den Schrittmodus geaendert hat, wird dieser zuerst
          auf das Geraet geschrieben und rueckgelesen.
        """

        normalized = self._normalize_motor_command(motor)
        self._apply_normalized_values_to_state(motor, normalized)

        self._write(motor.address, "p1")
        self._apply_step_mode_if_needed(motor, force=False)
        self._write(motor.address, f"o{int(motor.target_speed)}")

        target_steps = self._target_steps(motor, use_active_step_mode=True)
        self._write(motor.address, f"s{target_steps}")
        self._write(motor.address, f"W{int(motor.loops)}")
        self._write(motor.address, "t1")
        self._write(motor.address, "A")

        pos_raw = self._query(motor.address, "C")
        pos = self._parse_prefixed_int(pos_raw, "C")
        if pos is not None:
            motor.initial_position_steps = pos
            motor.actual_position_mm = self._steps_to_mm(pos, motor)

        motor.expected_runtime_sec = self._expected_runtime(motor, target_steps)
        motor.runtime_sec = 0.0
        motor.rest_sec = motor.expected_runtime_sec
        motor.running = True
        motor.status_text = "Motor running"
        motor.start_time_s = time.monotonic() if now_s is None else now_s

    def stop_profile(self, motor: MotorState) -> None:
        """Sofort-Stop der Profilfahrt."""

        self._write(motor.address, "S")
        motor.running = False
        motor.runtime_sec = 0.0
        motor.rest_sec = 0.0
        motor.status_text = "Stopped by user"

    def start_reference(self, motor: MotorState) -> None:
        """
        Startet eine Referenzfahrt (Legacy-C++-Logik).

        Hinweis:
        - Die Richtungsabbildung folgt bewusst dem vorhandenen Anlagenverhalten:
          `RIGHT -> d0`, `LEFT -> d1`.
        """

        self._write(motor.address, "o1000")
        self._write(motor.address, "d0" if motor.reference_direction == MotorDirection.RIGHT else "d1")
        self._write(motor.address, "W1")
        self._write(motor.address, "p4")
        self._write(motor.address, "A")
        motor.running = True
        motor.status_text = "Reference run"
        motor.start_time_s = time.monotonic()
        motor.runtime_sec = 0.0
        motor.rest_sec = 0.0

    def poll_motor(self, motor: MotorState, *, now_s: float | None = None) -> None:
        """
        Liest den aktuellen Motorzustand zyklisch aus.

        Diese Methode ist bewusst robust gegen Teilantworten: Wenn einzelne Felder
        nicht parsebar sind, bleiben die letzten gueltigen State-Werte erhalten.
        """

        now_value = time.monotonic() if now_s is None else now_s

        status_raw = self._query(motor.address, "$")
        status_code = self._parse_prefixed_int(status_raw, "$")
        if status_code is not None:
            motor.status_code = status_code
            motor.running = status_code in _STATUS_RUNNING
            motor.status_text = self._status_text_from_code(status_code)

            if not motor.running:
                motor.runtime_sec = 0.0
                motor.rest_sec = 0.0

        if motor.running and motor.expected_runtime_sec > 0:
            motor.runtime_sec = max(0.0, now_value - motor.start_time_s)
            motor.rest_sec = max(0.0, motor.expected_runtime_sec - motor.runtime_sec)

        pos_raw = self._query(motor.address, "C")
        pos = self._parse_prefixed_int(pos_raw, "C")
        if pos is not None:
            motor.actual_position_mm = self._steps_to_mm(pos, motor)

        # Schrittmodus nur auslesen, wenn der Motor nicht laeuft.
        # Das entspricht dem Legacy-Design: waehrend Fahrt keine unnötigen
        # Parametervarianten umschalten.
        if not motor.running:
            mode_raw = self._query(motor.address, "Zg")
            mode = self._parse_prefixed_int(mode_raw, "g")
            if mode is not None:
                motor.step_mode_active = mode

        enc_raw = self._query(motor.address, "I")
        enc = self._parse_prefixed_int(enc_raw, "I")
        if enc is not None and abs(motor.encoder_calibration) > 1.0e-12 and motor.step_mode_active != 0:
            motor.encoder_position_mm = enc / (motor.step_mode_active * motor.encoder_calibration)

    def normalize_step_mode(self, step_mode: int) -> int:
        """
        Validiert einen Schrittmoduswert gegen die erlaubte Liste.
        """

        candidate = int(step_mode)
        if candidate not in self.ALLOWED_STEP_MODES:
            allowed = ", ".join(str(v) for v in self.ALLOWED_STEP_MODES)
            raise NanotecValidationError(f"Invalid step mode {candidate}. Allowed: {allowed}")
        return candidate

    def normalize_target_speed(self, speed: int) -> int:
        """
        Validiert die Zielgeschwindigkeit.

        Bereich orientiert sich am Legacy-UI (1..1100).
        """

        candidate = int(speed)
        if candidate < self.MIN_SPEED or candidate > self.MAX_SPEED:
            raise NanotecValidationError(
                f"Invalid target speed {candidate}. Allowed range: {self.MIN_SPEED}..{self.MAX_SPEED}"
            )
        return candidate

    def normalize_loops(self, loops: int) -> int:
        """
        Validiert die Anzahl Wiederholungen.
        """

        candidate = int(loops)
        if candidate < self.MIN_LOOPS or candidate > self.MAX_LOOPS:
            raise NanotecValidationError(
                f"Invalid loops {candidate}. Allowed range: {self.MIN_LOOPS}..{self.MAX_LOOPS}"
            )
        return candidate

    def normalize_target_position_mm(self, position_mm: float) -> float:
        """
        Validiert den Zielweg auf numerische Plausibilitaet.
        """

        candidate = float(position_mm)
        if not math.isfinite(candidate):
            raise NanotecValidationError("Target position must be a finite number")
        if candidate < 0.0:
            raise NanotecValidationError(
                "Target position must be >= 0 mm. "
                "The travel direction is selected separately via MotorDirection."
            )
        if abs(candidate) > self.MAX_ABS_TARGET_MM:
            raise NanotecValidationError(
                f"Target position {candidate} mm exceeds allowed magnitude {self.MAX_ABS_TARGET_MM} mm"
            )
        return candidate

    def _normalize_motor_command(self, motor: MotorState) -> NormalizedMotorCommand:
        """
        Erstellt ein validiertes Befehlsbild aus dem aktuellen MotorState.
        """

        return NormalizedMotorCommand(
            target_speed=self.normalize_target_speed(motor.target_speed),
            target_position_mm=self.normalize_target_position_mm(motor.target_position_mm),
            step_mode_to_set=self.normalize_step_mode(motor.step_mode_to_set),
            loops=self.normalize_loops(motor.loops),
        )

    @staticmethod
    def _apply_normalized_values_to_state(motor: MotorState, normalized: NormalizedMotorCommand) -> None:
        """
        Schreibt normalisierte Werte zurueck in den MotorState.
        """

        motor.target_speed = normalized.target_speed
        motor.target_position_mm = normalized.target_position_mm
        motor.step_mode_to_set = normalized.step_mode_to_set
        motor.loops = normalized.loops

    def _apply_step_mode_if_needed(self, motor: MotorState, *, force: bool) -> None:
        """
        Schreibt `g<mode>` nur dann, wenn erforderlich (oder bei `force=True`).

        Nach dem Schreiben wird mit `Zg` zurueckgelesen, damit `step_mode_active`
        dem echten Geraetezustand entspricht.
        """

        desired = self.normalize_step_mode(motor.step_mode_to_set)
        if not force and desired == motor.step_mode_active:
            return

        self._write(motor.address, f"g{desired}")
        mode_raw = self._query(motor.address, "Zg")
        mode = self._parse_prefixed_int(mode_raw, "g")
        if mode is None:
            raise RuntimeError("Nanotec step mode readback failed after g-command")
        motor.step_mode_active = mode

    def _query(self, address: str, body: str, *, delay: float = 0.1) -> bytes:
        return self.transport.query(
            self.settings,
            protocols.nanotec_cmd(address, body),
            read_size=100,
            delay_after_write=delay,
        )

    def _write(self, address: str, body: str, *, delay: float = 0.1) -> None:
        self.transport.write(
            self.settings,
            protocols.nanotec_cmd(address, body),
            delay_after_write=delay,
        )

    @staticmethod
    def _parse_prefixed_int(raw: bytes, marker: str) -> int | None:
        text = raw.decode("latin1", errors="ignore").replace("\r", "").replace("\n", "")
        if not text:
            return None
        if marker in text:
            text = text.split(marker, 1)[1]
        match = re.search(r"-?\d+", text)
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    @staticmethod
    def _steps_to_mm(steps: int, motor: MotorState) -> float:
        # Legacy-Mapping beibehalten.
        if abs(motor.calibration) < 1.0e-12:
            return motor.actual_position_mm
        if motor.step_mode_active == 0:
            return steps / motor.calibration
        return steps / (10000.0 * motor.step_mode_active) * motor.calibration

    @staticmethod
    def _target_steps(motor: MotorState, *, use_active_step_mode: bool) -> int:
        # Legacy-Richtungsabbildung: Vorzeichen der Schrittzahl bestimmt Fahrtrichtung.
        step_mode = motor.step_mode_active if use_active_step_mode else motor.step_mode_to_set
        step_mode = step_mode if step_mode != 0 else 1
        calibration = motor.calibration if abs(motor.calibration) > 1.0e-12 else 1.0
        pos = abs(motor.target_position_mm) * 10000.0 * step_mode / calibration
        if motor.direction == MotorDirection.RIGHT:
            pos = -pos
        return int(pos)

    @staticmethod
    def _expected_runtime(motor: MotorState, target_steps: int) -> float:
        speed = max(1, int(motor.target_speed))
        return abs(float(target_steps)) * max(1, int(motor.loops)) / speed

    @staticmethod
    def _status_text_from_code(status_code: int) -> str:
        if status_code in _STATUS_RUNNING:
            return "Motor running"
        if status_code == 163:
            return "Referenced"
        if status_code in _STATUS_READY:
            return "Controller ready"
        return f"Fault/unknown status ({status_code})"
