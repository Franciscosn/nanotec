from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class RegulationMode(str, Enum):
    FEHLER = "Fehler"
    POWER = "Power"
    VOLTAGE = "Voltage"
    CURRENT = "Current"


REGULATION_CODE_TO_MODE = {
    0: RegulationMode.FEHLER,
    6: RegulationMode.POWER,
    7: RegulationMode.VOLTAGE,
    8: RegulationMode.CURRENT,
}

MODE_TO_REGULATION_CODE = {
    RegulationMode.FEHLER: 0,
    RegulationMode.POWER: 6,
    RegulationMode.VOLTAGE: 7,
    RegulationMode.CURRENT: 8,
}


class MotorDirection(str, Enum):
    LEFT = "Left"
    RIGHT = "Right"


@dataclass
class PortRuntime:
    connected: bool = False
    ready: bool = True
    failed: bool = False
    last_error: str = ""


@dataclass
class MotorState:
    address: str
    connected: bool = True
    running: bool = False
    target_speed: int = 1000
    target_position_mm: float = 0.0
    actual_position_mm: float = 0.0
    actual_position_steps: int = 0
    encoder_position_mm: float = 0.0
    step_mode_to_set: int = 2
    step_mode_active: int = 2
    direction: MotorDirection = MotorDirection.LEFT
    reference_direction: MotorDirection = MotorDirection.LEFT
    loops: int = 1
    calibration: float = 100.0
    encoder_calibration: float = -1.32
    expected_runtime_sec: float = 0.0
    runtime_sec: float = 0.0
    rest_sec: float = 0.0
    start_time_s: float = 0.0
    initial_position_steps: int = 0
    status_code: int = 17
    status_text: str = "Steuerung bereit"
    # Endschalter-/Tasterstatus aus der Expert-Ruecklese.
    #
    # Bedeutung:
    # - True  -> der jeweilige Schalter meldet "aktiv"
    # - False -> der jeweilige Schalter meldet "inaktiv"
    #
    # Diese beiden Felder werden im Controller bei jedem Tick aktualisiert und
    # dienen als zentrale, gut sichtbare Safety-Information fuer GUI und Logik.
    limit_left_active: bool = False
    limit_right_active: bool = False
    # Merkt die letzte automatische Stop-Ursache.
    # Beispiel: "left limit", "right limit", ""
    limit_stop_reason: str = ""

    @property
    def step_mode(self) -> int:
        return self.step_mode_active

    @property
    def progress_percent(self) -> float:
        """
        Liefert einen stabilen Fortschrittswert fuer GUI-Progressbars.

        Rueckgabebereich:
        - 0.0 bis 100.0
        """

        if self.expected_runtime_sec <= 0.0:
            return 0.0
        pct = 100.0 * max(0.0, self.runtime_sec) / self.expected_runtime_sec
        return max(0.0, min(100.0, pct))

    @property
    def referenced(self) -> bool:
        """
        True, wenn der letzte bekannte Statuscode den referenzierten Zustand zeigt.
        """

        return int(self.status_code) == 163


@dataclass
class FUGState:
    hv_on: bool = False
    voltage_set: float = 1200.0
    voltage_ramp: float = 100.0
    current_set: float = 0.03
    current_ramp: float = 0.006
    voltage_actual: float = 0.0
    current_actual: float = 0.0


@dataclass
class PinnacleChannelState:
    # Geraeteadresse des Kanals im Pinnacle-Protokoll (1..255).
    address: int

    # Gewuenschter Output-Zustand (Soll):
    # - True  -> Ausgang soll EIN sein
    # - False -> Ausgang soll AUS sein
    active: bool = False

    # Gewuenschter Regelmodus (Soll), z. B. Current/Voltage/Power.
    mode: RegulationMode = RegulationMode.CURRENT

    # Roh-Index fuer Pulsfrequenz (Protokoll-Byte, 0..255).
    pulse_frequency_index: int = 0

    # Roh-Index fuer Pulse-Reverse-Zeit (Protokoll-Byte, 0..255).
    pulse_reverse_index: int = 0

    # Gewuenschter Setpoint (Soll) in fachlicher Darstellung.
    setpoint: float = 1.0

    # ---------------- Istwerte / Readback vom Geraet ----------------
    # Aktuelle Pulsfrequenz in kHz (aus Index rueckgerechnet).
    act_pulse_frequency: int = 0
    # Geraeteinterner Modus-Code (0/6/7/8 ...).
    act_regulation_mode_code: int = 8
    # Aktuelle Pulse-Reverse-Zeit in us (aus Index rueckgerechnet).
    act_pulse_reverse_time: float = 0.0
    # Aktueller Setpoint laut Geraet (Readback, skaliert).
    setpoint_actual: float = 0.0
    # Elektrische Istwerte.
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0
    # Menschenlesbarer Modustext fuer GUI.
    regulation: str = RegulationMode.CURRENT.value

    # Kommunikationsdiagnose pro Kanal.
    comm_ok: bool = False
    last_error: str = ""

    @property
    def pulse_freq_khz(self) -> int:
        return self.pulse_frequency_index * 5

    @pulse_freq_khz.setter
    def pulse_freq_khz(self, value: int) -> None:
        self.pulse_frequency_index = max(0, min(255, int(round(value / 5.0))))

    @property
    def pulse_reverse_us(self) -> float:
        return self.pulse_reverse_index * 0.1

    @pulse_reverse_us.setter
    def pulse_reverse_us(self, value: float) -> None:
        self.pulse_reverse_index = max(0, min(255, int(round(value / 0.1))))


@dataclass
class VacuumState:
    # Hauptdruckwerte, die im gesamten Anlagenzustand genutzt werden.
    p_chamber: float = 0.0
    p_load: float = 0.0

    # Zugehörige Statuscodes der letzten Pfeiffer-Abfrage.
    # 6 steht für "unbekannt / nicht identifiziert" als sicherer Startwert.
    p_chamber_status: int = 6
    p_load_status: int = 6

    # Monotonic-Zeitstempel (Sekunden), wann die letzte valide Druckabfrage
    # in den State geschrieben wurde.
    # 0.0 bedeutet "noch keine Messung vorhanden".
    last_update_monotonic_s: float = 0.0

    p_baratron: float = 0.0
    chamber_sensor_on: bool = True
    load_sensor_on: bool = True

    # Standard jetzt bewusst False:
    # Für Interlocks brauchen wir in der Regel sowohl Chamber- als auch Loaddruck.
    single_gauge: bool = False

    # Legacy simulation internals from the C++ code.
    sim_v10: float = 1000.0
    sim_v20: float = 1000.0


@dataclass
class ValveState:
    vat_chamber: int = 0  # 0 closed, 1 half, 2 open
    vat_load_open: bool = False
    bypass_chamber_open: bool = False
    bypass_load_open: bool = False
    back_valve_chamber_open: bool = False
    back_valve_load_open: bool = False
    gate_load_open: bool = False
    ar_valve_open: bool = False


@dataclass
class ExpertIOState:
    # Pending outputs for E9043 (shadow commands from UI/interlocks).
    e9043_di1: List[int] = field(default_factory=lambda: [0] * 8)
    e9043_di2: List[int] = field(default_factory=lambda: [0] * 8)
    e9043_di1_changed: List[bool] = field(default_factory=lambda: [False] * 8)
    e9043_di2_changed: List[bool] = field(default_factory=lambda: [False] * 8)

    # Readback outputs from cards.
    e9043_do1: List[int] = field(default_factory=lambda: [0] * 8)
    e9043_do2: List[int] = field(default_factory=lambda: [0] * 8)
    e9053_do1: List[int] = field(default_factory=lambda: [0] * 8)
    e9053_do2: List[int] = field(default_factory=lambda: [0] * 8)

    e9053_iter: int = 0

    argon_set: float = 0.0
    argon_set_last: float = 0.0
    argon_actual: float = 0.0
    argon_calibration: float = 0.05 / 1.37


@dataclass
class PlantState:
    motor1: MotorState = field(default_factory=lambda: MotorState(address="1", target_position_mm=600.0))
    motor2: MotorState = field(
        default_factory=lambda: MotorState(
            address="2",
            target_position_mm=100.0,
            loops=1,
            encoder_calibration=-3.3,
        )
    )
    fug: FUGState = field(default_factory=FUGState)
    pin_a: PinnacleChannelState = field(default_factory=lambda: PinnacleChannelState(address=8))
    pin_b: PinnacleChannelState = field(default_factory=lambda: PinnacleChannelState(address=148))
    vacuum: VacuumState = field(default_factory=VacuumState)
    valves: ValveState = field(default_factory=ValveState)
    expert: ExpertIOState = field(default_factory=ExpertIOState)

    ports: Dict[str, PortRuntime] = field(
        default_factory=lambda: {
            "nanotec": PortRuntime(connected=True),
            "dualg": PortRuntime(connected=True),
            "fug": PortRuntime(connected=False),
            "pinnacle": PortRuntime(connected=False),
            "expert": PortRuntime(connected=True),
        }
    )

    simulation: bool = True

    def vat_chamber_text(self) -> str:
        if self.valves.vat_chamber == 0:
            return "chamber closed"
        if self.valves.vat_chamber == 1:
            return "chamber opened_50%"
        return "chamber opened"
