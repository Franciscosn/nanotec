from __future__ import annotations

import os
import sys
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_optional_float(name: str) -> float | None:
    """
    Liest optional einen Float aus der Umgebung.

    Rueckgabe:
    - `None`, wenn die Variable nicht gesetzt ist oder leer ist.
    - `float`, wenn eine gueltige Zahl vorliegt.

    Warum diese Funktion?
    - Fuer manche Safety-Optionen (z. B. Software-Fahrgrenzen) wollen wir
      bewusst unterscheiden zwischen:
      1) "nicht konfiguriert"  -> keine zusaetzliche Begrenzung
      2) "konfiguriert"        -> harte Begrenzung aktiv
    """

    raw = os.getenv(name)
    if raw is None:
        return None
    token = raw.strip()
    if token == "":
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    """
    Liest einen String aus der Umgebung und erlaubt nur bekannte Optionen.

    Beispiel:
    - SPUTTER_PFEIFFER_CONTROLLER=maxigauge -> "maxigauge"
    - SPUTTER_PFEIFFER_CONTROLLER=foo       -> default
    """

    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in allowed:
        return value
    return default


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _env_bit_level(name: str, default: int) -> int:
    """
    Liest einen digitalen Aktivpegel aus der Umgebung.

    Rueckgabe:
    - 1 = Bitwert 1 bedeutet "aktiv"
    - 0 = Bitwert 0 bedeutet "aktiv" (invertierte Logik)

    Warum diese Option wichtig ist:
    - In der Praxis sind manche Endschalter als NO, andere als NC verdrahtet.
    - Mit dieser Einstellung koennen wir die Safety-Logik korrekt an die
      reale Verdrahtung anpassen, ohne Code aendern zu muessen.
    """

    value = _env_int(name, default)
    return 1 if int(value) != 0 else 0


SIMULATION: bool = _env_bool("SPUTTER_SIMULATION", True)
TIMER_INTERVAL_SEC: float = _env_float("SPUTTER_TIMER_INTERVAL_SEC", 0.5)
SIMULATION_SEED: int = _env_int("SPUTTER_SIM_SEED", 12345)
PRESSURE_MAX_AGE_SEC: float = _env_float("SPUTTER_PRESSURE_MAX_AGE_SEC", 3.0)

# Pfeiffer-Backend-Auswahl:
# - "tpg262": klassischer 2-Kanal-Dual-Gauge-Controller.
# - "maxigauge": 6-Kanal-MaxiGauge/TPG 256 A.
PFEIFFER_CONTROLLER: str = _env_choice(
    "SPUTTER_PFEIFFER_CONTROLLER",
    "maxigauge",
    {"tpg262", "maxigauge"},
)

# Single-Gauge-Modus nur für TPG262 relevant:
# True  -> nur PR1 (Load wird dann nicht aktiv gemessen)
# False -> PRX / PR1+PR2 (beide Drücke für Interlocks verfügbar)
PFEIFFER_SINGLE_GAUGE: bool = _env_bool("SPUTTER_PFEIFFER_SINGLE_GAUGE", False)

# Kanal-Mapping für MaxiGauge:
# Diese Werte bestimmen, welcher MaxiGauge-Kanal als Chamber/Load in den
# bestehenden Plant-State geschrieben wird.
PFEIFFER_MAXI_CHAMBER_CHANNEL: int = _clamp_int(_env_int("SPUTTER_MAXI_CHAMBER_CHANNEL", 1), 1, 6)
PFEIFFER_MAXI_LOAD_CHANNEL: int = _clamp_int(_env_int("SPUTTER_MAXI_LOAD_CHANNEL", 2), 1, 6)

BASE_DIR = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = BASE_DIR / "protocols"
LOCK_FILE = PROTOCOL_DIR / "lock.txt"


def _default_ports() -> dict[str, str]:
    # Keep original COM defaults on Windows and provide sensible placeholders on Unix.
    if sys.platform.startswith("win"):
        return {
            "nanotec": "COM4",
            "dualg": "COM6",
            "fug": "COM3",
            "pinnacle": "COM3",
            "expert": "COM3",
        }
    if sys.platform == "darwin":
        return {
            "nanotec": "/dev/tty.usbserial-nanotec",
            "dualg": "/dev/tty.usbserial-dualg",
            "fug": "/dev/tty.usbserial-fug",
            "pinnacle": "/dev/tty.usbserial-pinnacle",
            "expert": "/dev/tty.usbserial-expert",
        }
    return {
        "nanotec": "/dev/ttyUSB0",
        "dualg": "/dev/ttyUSB1",
        "fug": "/dev/ttyUSB2",
        "pinnacle": "/dev/ttyUSB3",
        "expert": "/dev/ttyUSB4",
    }


def _apply_port_overrides(ports: dict[str, str]) -> dict[str, str]:
    result = dict(ports)
    for key in tuple(result):
        env_key = f"SPUTTER_PORT_{key.upper()}"
        value = os.getenv(env_key)
        if value:
            result[key] = value.strip()
    return result


PORTS = _apply_port_overrides(_default_ports())

BAUD = {
    "nanotec": 115200,
    "dualg": 9600,
    "fug": 9600,
    "pinnacle": 9600,
    "expert": 38400,
}

# Per-device serial line settings for pyserial.
SERIAL = {
    "nanotec": {"parity": "N", "bytesize": 8, "stopbits": 1, "timeout": 0.8},
    "dualg": {"parity": "N", "bytesize": 8, "stopbits": 1, "timeout": 0.5},
    "fug": {"parity": "N", "bytesize": 8, "stopbits": 1, "timeout": 0.5},
    "pinnacle": {"parity": "O", "bytesize": 8, "stopbits": 1, "timeout": 0.5},
    "expert": {"parity": "N", "bytesize": 8, "stopbits": 1, "timeout": 0.5},
}

EXPERT_ADDR = {
    "e9043": "02",
    "e9053": "03",
    "e9024": "05",
}

NANOTEC_ADDRESSES = ("1", "2")

# Erlaubte Schrittmodi fuer die Nanotec-Motoren (entspricht Legacy-Dialog).
NANOTEC_STEP_MODES = (1, 2, 4, 5, 8, 10, 16, 32, 64, 254, 255)

# Optionale Taster-/Endschalterzuordnung aus den Expert-E9053-Ruecklesebits.
# Bitzaehlung:
# - 0..7  -> e9053_do1[0..7]
# - 8..15 -> e9053_do2[0..7]
# - -1    -> nicht verdrahtet / nicht genutzt
#
# Default:
# - Motor 1 nutzt historische Load-Carriage-Bits:
#   - 11: Referenz links
#   - 12: Endlage rechts
# - Motor 2 ist im aktuellen Backend nicht fest verdrahtet und startet mit -1/-1.
MOTOR1_LEFT_TASTER_BIT: int = _env_int("SPUTTER_MOTOR1_LEFT_TASTER_BIT", 11)
MOTOR1_RIGHT_TASTER_BIT: int = _env_int("SPUTTER_MOTOR1_RIGHT_TASTER_BIT", 12)
MOTOR2_LEFT_TASTER_BIT: int = _env_int("SPUTTER_MOTOR2_LEFT_TASTER_BIT", -1)
MOTOR2_RIGHT_TASTER_BIT: int = _env_int("SPUTTER_MOTOR2_RIGHT_TASTER_BIT", -1)

# Aktivpegel fuer die Taster-/Endschalterbits:
# - 1: Bitwert 1 bedeutet "aktiv"
# - 0: Bitwert 0 bedeutet "aktiv" (invertierte Verdrahtung / NC)
MOTOR1_LEFT_TASTER_ACTIVE_LEVEL: int = _env_bit_level("SPUTTER_MOTOR1_LEFT_TASTER_ACTIVE_LEVEL", 1)
MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL: int = _env_bit_level("SPUTTER_MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL", 1)
MOTOR2_LEFT_TASTER_ACTIVE_LEVEL: int = _env_bit_level("SPUTTER_MOTOR2_LEFT_TASTER_ACTIVE_LEVEL", 1)
MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL: int = _env_bit_level("SPUTTER_MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL", 1)

# Optionale Software-Fahrgrenzen in mm (zusaetzlich zu den physischen Endschaltern).
#
# Sicherheitsidee:
# - Endschalter sind die wichtigste physische Schutzebene.
# - Software-Fahrgrenzen sind eine zweite, logische Schutzschicht gegen
#   Fehlkonfigurationen, Tippfehler oder unerwartete Referenzlagen.
#
# Verhalten:
# - `None`  -> keine Begrenzung auf dieser Seite aktiv.
# - Zahl    -> Begrenzung aktiv.
#
# Beispiel:
#   SPUTTER_MOTOR1_SOFT_MIN_MM=-10
#   SPUTTER_MOTOR1_SOFT_MAX_MM=620
MOTOR1_SOFT_MIN_MM: float | None = _env_optional_float("SPUTTER_MOTOR1_SOFT_MIN_MM")
MOTOR1_SOFT_MAX_MM: float | None = _env_optional_float("SPUTTER_MOTOR1_SOFT_MAX_MM")
MOTOR2_SOFT_MIN_MM: float | None = _env_optional_float("SPUTTER_MOTOR2_SOFT_MIN_MM")
MOTOR2_SOFT_MAX_MM: float | None = _env_optional_float("SPUTTER_MOTOR2_SOFT_MAX_MM")

# Safety thresholds derived from existing logic
PRESSURE = {
    "valve_open_max": 0.3,
    "valve_open_min": 2.0e-8,
    "bypass_min": 5.0e-3,
    "argon_max_for_open": 1.0e-5,
    "max_age_sec": PRESSURE_MAX_AGE_SEC,
}

PROTOCOL_HEADER = [
    "Data",
    "Time",
    "chamber_VAT_state",
    "act_step_mode",
    "Target_speed",
    "Actual_position_mm",
    "act_step_mode",
    "Target_speed",
    "Actual_position_mm",
    "FUG_Spannung",
    "FUG_Strom",
    "PinnacleA_actPulseFrequency",
    "PinnacleA_actRegulationMode",
    "PinnacleA_actPulseReverseTime",
    "PinnacleA_Voltage",
    "PinnacleA_Current",
    "PinnacleA_power",
    "PinnacleB_actPulseFrequency",
    "PinnacleB_actRegulationMode",
    "PinnacleB_actPulseReverseTime",
    "PinnacleB_Voltage",
    "PinnacleB_Current",
    "PinnacleB_power",
    "Ar_valve",
    "Baratron pressure",
    "Pressure_loadlock",
    "Pressure_Chamber",
]
