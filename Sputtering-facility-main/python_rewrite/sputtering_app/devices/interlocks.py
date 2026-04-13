from __future__ import annotations

import time
from dataclasses import dataclass

from ..models import ExpertIOState, PlantState, PortRuntime


@dataclass(frozen=True)
class PressureThresholds:
    """
    Bündelt alle relevanten Grenzwerte für Ventil-/Argon-Interlocks.

    Die Werte sind bewusst an einer Stelle gesammelt, damit
    1) Sicherheitsentscheidungen zentral nachvollziehbar sind,
    2) ein späteres Tuning nicht in vielen Dateien verteilt werden muss.
    """

    valve_open_max: float
    valve_open_min: float
    bypass_min: float
    argon_max_for_open: float

    # Neue Schutzgrenze:
    # Druckwerte dürfen nicht "zu alt" sein, wenn sie als Freigabekriterium dienen.
    pressure_max_age_sec: float


class PlantInterlocks:
    """
    Enthält die komplette Sicherheitslogik für das Vakuum-Pumpen-Bedienteil.

    Leitidee:
    - Diese Klasse entscheidet nur "darf umgeschaltet werden?" und schreibt dann
      den gewünschten Sollzustand in den Plant-State.
    - Die physische Ausgabe auf Expert-Karten geschieht getrennt im `ExpertDevice`.
    - Durch die Trennung bleibt der Code modular und testbar.
    """

    # Annahme für Gate-Load-Ansteuerung:
    # - DI1[2] wird als Sollbit "Gate open request" verwendet.
    # - 1 => Gate soll offen sein
    # - 0 => Gate soll geschlossen sein
    # Dieser Punkt war im historischen Code nicht vollständig ausdefiniert; die
    # Zuordnung ist deshalb bewusst hier zentral dokumentiert.
    _GATE_LOAD_DI1_INDEX = 2

    def __init__(self, thresholds: PressureThresholds) -> None:
        self._p = thresholds

    def toggle_bypass_load(self, state: PlantState) -> tuple[bool, str]:
        """
        Toggle Bypass-Ventil der Load-Seite.

        Sicherheitslogik:
        - Schließen ist nur möglich, wenn Ventilausgabe steuerbar ist.
        - Öffnen braucht zusätzlich frische/gültige Druckdaten und Mindestdruck.
        """

        valves = state.valves
        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="Bypass load")
        if not output_ok:
            return False, output_msg

        if valves.vat_load_open:
            return False, "Bypass load blocked: VAT load is open"

        if valves.bypass_load_open:
            valves.bypass_load_open = False
            self._set_e9043_di1(state.expert, 0, 0)
            return True, "Bypass load closed"

        pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="Bypass load open")
        if not pressure_ok:
            return False, pressure_msg

        if state.vacuum.p_load <= self._p.bypass_min:
            return False, "Bypass load blocked: load pressure too low"

        valves.bypass_load_open = True
        self._set_e9043_di1(state.expert, 0, 1)
        return True, "Bypass load opened"

    def toggle_vat_load(self, state: PlantState) -> tuple[bool, str]:
        """
        Toggle VAT-Load.

        Öffnen nur bei:
        - Ausgangspfad bereit,
        - Druckpfad bereit,
        - Back-Valve offen,
        - Druck im zulässigen Bereich.
        """

        valves = state.valves
        vac = state.vacuum

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="VAT load")
        if not output_ok:
            return False, output_msg

        if valves.bypass_load_open:
            return False, "VAT load blocked: bypass load is open"

        if valves.vat_load_open:
            valves.vat_load_open = False
            self._set_e9043_di2(state.expert, 0, 0)
            return True, "VAT load closed"

        pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="VAT load open")
        if not pressure_ok:
            return False, pressure_msg

        in_range = (
            vac.load_sensor_on
            and self._is_interlock_status_ok(vac.p_load_status)
            and vac.p_load < self._p.valve_open_max
            and vac.p_load > self._p.valve_open_min
        )
        if not (valves.back_valve_load_open and in_range):
            return False, "VAT load blocked: pressure/back valve condition not met"

        valves.vat_load_open = True
        self._set_e9043_di2(state.expert, 0, 1)
        return True, "VAT load opened"

    def toggle_back_valve_load(self, state: PlantState) -> tuple[bool, str]:
        """
        Toggle Back-Valve Load.

        Verbesserung gegenüber altem Stand:
        - Schließen wird auch bei fehlender Druckfreigabe erlaubt (fail-safe close).
        - Öffnen benötigt frische, gültige Druckdaten.
        """

        vac = state.vacuum
        valves = state.valves

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="Back valve load")
        if not output_ok:
            return False, output_msg

        if valves.back_valve_load_open:
            valves.back_valve_load_open = False
            self._set_e9043_di1(state.expert, 1, 0)
            return True, "Back valve load closed"

        pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="Back valve load open")
        if not pressure_ok:
            return False, pressure_msg

        if not (
            vac.load_sensor_on
            and self._is_interlock_status_ok(vac.p_load_status)
            and vac.p_load < self._p.valve_open_max
            and vac.p_load > self._p.valve_open_min
        ):
            return False, "Back valve load blocked: pressure condition not met"

        valves.back_valve_load_open = True
        self._set_e9043_di1(state.expert, 1, 1)
        return True, "Back valve load opened"

    def toggle_bypass_chamber(self, state: PlantState) -> tuple[bool, str]:
        valves = state.valves

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="Bypass chamber")
        if not output_ok:
            return False, output_msg

        if valves.vat_chamber != 0:
            return False, "Bypass chamber blocked: VAT chamber is not closed"

        if valves.bypass_chamber_open:
            valves.bypass_chamber_open = False
            self._set_e9043_di2(state.expert, 6, 0)
            return True, "Bypass chamber closed"

        pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="Bypass chamber open")
        if not pressure_ok:
            return False, pressure_msg

        if state.vacuum.p_chamber <= self._p.bypass_min:
            return False, "Bypass chamber blocked: chamber pressure too low"

        valves.bypass_chamber_open = True
        self._set_e9043_di2(state.expert, 6, 1)
        return True, "Bypass chamber opened"

    def set_vat_chamber(self, state: PlantState, mode: int) -> tuple[bool, str]:
        """
        Setzt VAT-Chamber auf 0/1/2 (closed/half/open).

        Sicherheitsprinzip:
        - Jede Umschaltung braucht ausgangsseitige Steuerbarkeit.
        - Öffnen/Halböffnen braucht zusätzlich frische und gültige Chamber-Druckdaten.
        """

        mode = int(mode)
        if mode not in (0, 1, 2):
            return False, "Invalid VAT chamber mode"

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="VAT chamber change")
        if not output_ok:
            return False, output_msg

        valves = state.valves
        vac = state.vacuum

        if valves.bypass_chamber_open and mode != 0:
            return False, "VAT chamber change blocked: bypass chamber is open"

        if mode in (1, 2):
            pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="VAT chamber open/half")
            if not pressure_ok:
                return False, pressure_msg

            in_range = (
                vac.chamber_sensor_on
                and self._is_interlock_status_ok(vac.p_chamber_status)
                and vac.p_chamber < self._p.valve_open_max
                and vac.p_chamber > self._p.valve_open_min
            )
            if not (valves.back_valve_chamber_open and in_range):
                return False, "VAT chamber change blocked: pressure/back valve condition not met"

        if valves.vat_chamber == mode:
            return True, "VAT chamber already in requested state"

        valves.vat_chamber = mode
        if mode == 2:
            self._set_e9043_di1(state.expert, 3, 0)
            self._set_e9043_di2(state.expert, 0, 1)
            return True, "VAT chamber opened"
        if mode == 1:
            self._set_e9043_di1(state.expert, 3, 0)
            self._set_e9043_di2(state.expert, 0, 0)
            return True, "VAT chamber set to half-open"

        self._set_e9043_di1(state.expert, 3, 1)
        self._set_e9043_di2(state.expert, 0, 0)
        return True, "VAT chamber closed"

    def toggle_back_valve_chamber(self, state: PlantState) -> tuple[bool, str]:
        vac = state.vacuum
        valves = state.valves

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="Back valve chamber")
        if not output_ok:
            return False, output_msg

        if valves.back_valve_chamber_open and valves.vat_chamber in (1, 2):
            return False, "Back valve chamber cannot close while VAT chamber is open/half-open"

        if valves.back_valve_chamber_open:
            valves.back_valve_chamber_open = False
            self._set_e9043_di2(state.expert, 7, 0)
            return True, "Back valve chamber closed"

        pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="Back valve chamber open")
        if not pressure_ok:
            return False, pressure_msg

        if not (
            vac.chamber_sensor_on
            and self._is_interlock_status_ok(vac.p_chamber_status)
            and vac.p_chamber < self._p.valve_open_max
            and vac.p_chamber > self._p.valve_open_min
        ):
            return False, "Back valve chamber blocked: pressure condition not met"

        valves.back_valve_chamber_open = True
        self._set_e9043_di2(state.expert, 7, 1)
        return True, "Back valve chamber opened"

    def toggle_gate_load(self, state: PlantState) -> tuple[bool, str]:
        """
        Toggle Gate Load (Transfer-Gate zwischen Loadlock und Chamber).

        Problembehebung:
        - Jetzt wird nicht nur der boolesche Zustand gesetzt, sondern auch ein
          explizites Expert-Ausgangsbit für den Gate-Sollzustand geschrieben.
        """

        vac = state.vacuum
        valves = state.valves

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="Gate load")
        if not output_ok:
            return False, output_msg

        if not valves.gate_load_open:
            pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="Gate load open")
            if not pressure_ok:
                return False, pressure_msg

            if vac.p_load > 5.0 * max(vac.p_chamber, 1.0e-12):
                return False, "Gate open blocked: pressure ratio too high"
            if vac.p_load > 2.0e-5 and vac.p_chamber < 1.0e-5:
                return False, "Gate open blocked: pressure condition not met"

            valves.gate_load_open = True
            self._set_e9043_di1(state.expert, self._GATE_LOAD_DI1_INDEX, 1)
            return True, "Gate load opened"

        valves.gate_load_open = False
        self._set_e9043_di1(state.expert, self._GATE_LOAD_DI1_INDEX, 0)
        return True, "Gate load closed"

    def toggle_argon(self, state: PlantState) -> tuple[bool, str]:
        valves = state.valves

        output_ok, output_msg = self._ensure_output_path_ready(state, action_label="Argon valve")
        if not output_ok:
            return False, output_msg

        if valves.ar_valve_open:
            valves.ar_valve_open = False
            self._set_e9043_di2(state.expert, 4, 0)
            return True, "Argon valve closed"

        pressure_ok, pressure_msg = self._ensure_pressure_path_ready(state, action_label="Argon open")
        if not pressure_ok:
            return False, pressure_msg

        if state.vacuum.p_chamber >= self._p.argon_max_for_open:
            return False, "Argon not opened: chamber pressure too high"

        valves.ar_valve_open = True
        self._set_e9043_di2(state.expert, 4, 1)
        return True, "Argon valve opened"

    def _ensure_output_path_ready(self, state: PlantState, *, action_label: str) -> tuple[bool, str]:
        """
        Prüft, ob Ventilkommandos sinnvoll an die Hardware gesendet werden können.

        In Simulation wird absichtlich immer freigegeben.
        Im Realbetrieb muss die Expert-Karte verbunden und fehlerfrei sein.
        """

        if state.simulation:
            return True, ""
        status = self._port(state, "expert")
        if not status.connected or status.failed:
            return False, f"{action_label} blocked: Expert I/O backend is not connected/healthy"
        return True, ""

    def _ensure_pressure_path_ready(self, state: PlantState, *, action_label: str) -> tuple[bool, str]:
        """
        Prüft, ob Druckwerte für Sicherheitsentscheidungen vertrauenswürdig sind.

        Bedingungen im Realbetrieb:
        1) Gauge-Port verbunden und nicht im Fehlerzustand.
        2) Es gibt bereits mindestens eine Messung.
        3) Die letzte Messung ist nicht älter als `pressure_max_age_sec`.
        """

        if state.simulation:
            return True, ""

        gauge_status = self._port(state, "dualg")
        if not gauge_status.connected or gauge_status.failed:
            return False, f"{action_label} blocked: pressure gauge backend is not connected/healthy"

        ts = state.vacuum.last_update_monotonic_s
        if ts <= 0.0:
            return False, f"{action_label} blocked: no pressure sample available yet"

        age = time.monotonic() - ts
        if age > self._p.pressure_max_age_sec:
            return (
                False,
                f"{action_label} blocked: pressure data too old ({age:.2f}s > {self._p.pressure_max_age_sec:.2f}s)",
            )
        return True, ""

    @staticmethod
    def _is_interlock_status_ok(status_code: int) -> bool:
        """
        Definiert, welche Pfeiffer-Statuscodes für Interlock-Entscheidungen akzeptiert werden.

        Erlaubt:
        - 0 (ok)
        - 1 (underrange)
        - 2 (overrange)

        Nicht erlaubt:
        - 3 (sensor error)
        - 4 (sensor off)
        - 5/6 (sensor fehlt / Identifikationsproblem)
        """

        return status_code in (0, 1, 2)

    @staticmethod
    def _port(state: PlantState, name: str) -> PortRuntime:
        return state.ports[name]

    @staticmethod
    def _set_e9043_di1(expert: ExpertIOState, idx: int, value: int) -> None:
        expert.e9043_di1[idx] = 1 if value else 0
        expert.e9043_di1_changed[idx] = True

    @staticmethod
    def _set_e9043_di2(expert: ExpertIOState, idx: int, value: int) -> None:
        expert.e9043_di2[idx] = 1 if value else 0
        expert.e9043_di2_changed[idx] = True

