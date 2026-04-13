from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from .. import protocols
from ..models import VacuumState
from .transport import ExchangeStep, SerialDeviceTransport, SerialSettings


# Pfeiffer-Protokoll-Steuerzeichen:
# - ACK (0x06): Der Befehl wurde vom Controller akzeptiert.
# - NAK (0x15): Der Befehl wurde abgelehnt.
_ACK_BYTE = 0x06
_NAK_BYTE = 0x15


@dataclass(frozen=True)
class GaugeReading:
    """
    Repräsentiert genau einen Messwert eines Gauge-Kanals.

    status:
        Pfeiffer-Statuscode (0=ok, 1=underrange, 2=overrange, 4=sensor off, ...).
    value:
        Zahlenwert des Drucks (typischerweise in der am Controller eingestellten Einheit).
    """

    status: int
    value: float


class PfeifferGaugeDeviceProtocol(Protocol):
    """
    Gemeinsame minimale Schnittstelle für Pfeiffer-Geraete im Sputtering-Backend.

    Warum als Protocol?
    - Der Controller soll unabhängig davon sein, ob wir TPG262 oder MaxiGauge nutzen.
    - Beide Klassen müssen dieselben Kernfunktionen anbieten.
    """

    def check_connection(self) -> bool:
        """Prüft, ob das Gerät antwortet und mindestens ein plausibler Messwert gelesen werden kann."""

    def query_pressures(self, state: VacuumState) -> tuple[float, float]:
        """
        Aktualisiert `state.vacuum` und liefert (chamber, load) zurück.

        Die Methode ist absichtlich kompatibel zur bisherigen `DualGaugeDevice`-API.
        """

    def set_chamber_sensor(self, on: bool) -> None:
        """Schaltet den Chamber-Sensor logisch ein/aus (soweit vom Controller erlaubt)."""

    def set_load_sensor(self, on: bool) -> None:
        """Schaltet den Load-Sensor logisch ein/aus (soweit vom Controller erlaubt)."""

    def query_ascii(self, command: str) -> str:
        """Fuehrt einen rohen Lesebefehl aus (ACK + ENQ + Daten)."""

    def write_ascii(self, command: str) -> None:
        """Fuehrt einen rohen Schreibbefehl aus (ACK/NAK, ohne Datenphase)."""

    def read_channel(self, channel: int) -> Optional[GaugeReading]:
        """Liest einen einzelnen PRx-Kanal und liefert Status+Wert."""

    def channel_count(self) -> int:
        """Liefert die Anzahl physisch adressierbarer Kanale des Geraets."""

    def get_unit(self) -> int:
        """Liest die aktuell aktive Druckeinheit."""

    def set_unit(self, unit_code: int) -> None:
        """Setzt die Druckeinheit (0=mbar, 1=Torr, 2=Pa)."""

    def get_sensor_onoff(self) -> list[int]:
        """Liest SEN als Liste (1=aus, 2=ein, 0=unveraendert/ungueltig)."""

    def set_sensor_onoff(self, gauge: int, turn_on: bool) -> None:
        """Schaltet einen Sensor kanalweise ein/aus."""

    def get_degas(self) -> list[int]:
        """Liest DGS-Statusliste."""

    def set_degas(self, gauge: int, on: bool) -> None:
        """Setzt DGS kanalweise."""

    def get_filter(self) -> list[int]:
        """Liest FIL-Statusliste."""

    def set_filter(self, gauge: int, value: int) -> None:
        """Setzt FIL kanalweise."""

    def set_calibration(self, gauge: int, value: float) -> None:
        """Setzt den Kalibrierfaktor kanalweise."""

    def get_fsr(self) -> list[int]:
        """Liest FSR-Statusliste."""

    def set_fsr(self, gauge: int, value: int) -> None:
        """Setzt FSR kanalweise."""

    def get_ofc(self) -> list[int]:
        """Liest OFC-Statusliste."""

    def set_ofc(self, gauge: int, value: int) -> None:
        """Setzt OFC kanalweise."""

    def get_ident(self) -> str:
        """Liest eine passende Identifikationsantwort des Geraets."""

    def get_channel_names(self) -> list[str]:
        """Liest Kanalnamen (falls unterstuetzt)."""

    def set_channel_name(self, gauge: int, name: str) -> None:
        """Setzt einen Kanalnamen (falls unterstuetzt)."""

    def get_digits(self) -> int:
        """Liest Digits-Anzeigeparameter (falls unterstuetzt)."""

    def set_digits(self, value: int) -> None:
        """Setzt Digits-Anzeigeparameter (falls unterstuetzt)."""

    def get_contrast(self) -> int:
        """Liest Display-Kontrast (falls unterstuetzt)."""

    def set_contrast(self, value: int) -> None:
        """Setzt Display-Kontrast (falls unterstuetzt)."""

    def get_screensave(self) -> int:
        """Liest Screensaver-Timeout (falls unterstuetzt)."""

    def set_screensave(self, value: int) -> None:
        """Setzt Screensaver-Timeout (falls unterstuetzt)."""

    def factory_reset(self) -> None:
        """Loest das passende SAV-Werkreset aus."""

    def device_info_lines(self) -> list[str]:
        """Liefert kompakte Diagnosezeilen fuer GUI/Logs."""


@dataclass
class PfeifferProtocolClient:
    """
    Kapselt das serielle Protokollmuster der Pfeiffer-Controller.

    Hintergrund:
    Ein typischer lesender Ablauf ist:
    1) ASCII-Kommando senden (z. B. "PR1\\r")
    2) ACK/NAK lesen und auswerten
    3) ENQ senden (0x05), um die Datenantwort anzufordern
    4) Daten lesen

    Ein typischer schreibender Ablauf ist:
    1) ASCII-Kommando senden
    2) ACK/NAK lesen
    3) Keine ENQ-Stufe, weil keine Datenantwort erwartet wird
    """

    transport: SerialDeviceTransport
    settings: SerialSettings
    ack_read_size: int = 32
    data_read_size: int = 256
    delay_after_command_s: float = 0.18
    delay_after_enq_s: float = 0.18

    def query_ascii_response(self, command: str) -> str:
        """
        Führt einen lesenden Kommandozyklus aus und gibt den Datenstring zurück.

        Fehlerfälle:
        - NAK: Gerät hat Befehl abgelehnt.
        - Kein ACK: vermutlich Kommunikations- oder Timingproblem.
        - Leere Datenantwort: Befehl wurde angenommen, aber es kamen keine Nutzdaten zurück.
        """

        payload = self._ascii_payload(command)
        responses = self.transport.exchange(
            self.settings,
            [
                ExchangeStep(
                    payload=payload,
                    read_size=self.ack_read_size,
                    delay_after_write=self.delay_after_command_s,
                ),
                ExchangeStep(
                    payload=protocols.dualg_enq(),
                    read_size=self.data_read_size,
                    delay_after_write=self.delay_after_enq_s,
                ),
            ],
        )

        ack_raw = responses[0] if len(responses) >= 1 else b""
        data_raw = responses[1] if len(responses) >= 2 else b""
        self._ensure_ack_or_raise(command, ack_raw)

        text = _normalize_ascii_payload(data_raw)
        if not text:
            raise RuntimeError(
                f"Pfeiffer command '{command}' returned no payload after ACK. "
                "Check cable/port and controller mode."
            )
        return text

    def write_ascii_command(self, command: str) -> None:
        """
        Führt einen schreibenden Kommandozyklus aus (nur ACK/NAK, keine Datenantwort).
        """

        payload = self._ascii_payload(command)
        responses = self.transport.exchange(
            self.settings,
            [
                ExchangeStep(
                    payload=payload,
                    read_size=self.ack_read_size,
                    delay_after_write=self.delay_after_command_s,
                ),
            ],
        )

        ack_raw = responses[0] if len(responses) >= 1 else b""
        self._ensure_ack_or_raise(command, ack_raw)

    @staticmethod
    def _ascii_payload(command: str) -> bytes:
        # Pfeiffer erwartet ASCII + CR am Ende.
        return command.encode("ascii") + b"\r"

    @staticmethod
    def _ensure_ack_or_raise(command: str, raw: bytes) -> None:
        if not raw:
            raise RuntimeError(f"Pfeiffer command '{command}' got no ACK/NAK response.")
        if _NAK_BYTE in raw:
            raise RuntimeError(
                f"Pfeiffer command '{command}' was rejected (NAK). "
                "Likely causes: command not supported for the connected sensor, "
                "invalid parameters, or remote switching not allowed."
            )
        if _ACK_BYTE not in raw:
            raise RuntimeError(
                f"Pfeiffer command '{command}' received data without ACK marker. Raw={raw!r}"
            )


@dataclass
class TPG262GaugeDevice:
    """
    Treiber für Pfeiffer TPG 262 (2 Kanäle).

    Designziele:
    - Robustes Status+Wert-Parsing für PR1/PR2 und PRX.
    - Explizite ACK/NAK-Auswertung.
    - Kompatibilität zur bisherigen Controller-API (`query_pressures`, Sensor-Toggle).
    """

    transport: SerialDeviceTransport
    settings: SerialSettings
    _client: PfeifferProtocolClient = field(init=False)

    def __post_init__(self) -> None:
        self._client = PfeifferProtocolClient(self.transport, self.settings)

    def check_connection(self) -> bool:
        reading = self.read_channel(channel=1)
        return reading is not None

    def query_pressures(self, state: VacuumState) -> tuple[float, float]:
        """
        Liest Chamber- und Loaddruck und schreibt sie in den gemeinsamen Plant-State.

        Wichtige Korrektur gegenüber dem früheren Rewrite:
        - Bei PR1/PR2 ist das Format `status,value`.
        - Der Zahlenwert ist also Feld 2, NICHT Feld 1.
        """

        # Vorwerte als sichere Fallbacks:
        # Falls ein einzelner Zyklus unvollständig ist, behalten wir den letzten gültigen Zustand.
        chamber_fallback = GaugeReading(status=state.p_chamber_status, value=state.p_chamber)
        load_fallback = GaugeReading(status=state.p_load_status, value=state.p_load)

        chamber_reading: Optional[GaugeReading] = None
        load_reading: Optional[GaugeReading] = None

        if state.single_gauge:
            # Single-Gauge-Modus: historisches Verhalten wie im C++-Code.
            chamber_reading = self.read_channel(channel=1)
        else:
            # Normaler Dual-Gauge-Modus:
            # PRX ist effizient, weil beide Kanaele in einer Antwort kommen.
            chamber_reading, load_reading = self._read_prx_pair()

            # Fallback auf Einzelabfragen, wenn PRX nicht sauber geparst werden konnte.
            if chamber_reading is None:
                chamber_reading = self.read_channel(channel=1)
            if load_reading is None:
                load_reading = self.read_channel(channel=2)

        chamber_final = self._finalize_reading(
            reading=chamber_reading,
            sensor_on=state.chamber_sensor_on,
            fallback=chamber_fallback,
        )
        load_final = self._finalize_reading(
            reading=load_reading,
            sensor_on=state.load_sensor_on,
            fallback=load_fallback,
        )

        state.p_chamber = chamber_final.value
        state.p_load = load_final.value
        state.p_chamber_status = chamber_final.status
        state.p_load_status = load_final.status
        return chamber_final.value, load_final.value

    def set_chamber_sensor(self, on: bool) -> None:
        self.set_sensor_onoff(gauge=1, turn_on=on)

    def set_load_sensor(self, on: bool) -> None:
        self.set_sensor_onoff(gauge=2, turn_on=on)

    def query_ascii(self, command: str) -> str:
        return self._client.query_ascii_response(command)

    def write_ascii(self, command: str) -> None:
        self._client.write_ascii_command(command)

    def read_channel(self, channel: int) -> Optional[GaugeReading]:
        _require_channel(channel, max_channel=2)
        text = self._client.query_ascii_response(f"PR{channel}")
        return _parse_status_value_text(text)

    @staticmethod
    def channel_count() -> int:
        return 2

    def get_unit(self) -> int:
        return int(float(self.query_ascii("UNI")))

    def set_unit(self, unit_code: int) -> None:
        self.write_ascii(f"UNI,{int(unit_code)}")

    def get_sensor_onoff(self) -> list[int]:
        text = self.query_ascii("SEN")
        vals = _parse_csv_ints(text)
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected SEN response for TPG262: {text!r}")
        return vals

    def set_sensor_onoff(self, gauge: int, turn_on: bool) -> None:
        _require_channel(gauge, max_channel=2)
        vals = [0, 0]
        vals[gauge - 1] = 2 if turn_on else 1
        self.write_ascii(f"SEN,{vals[0]},{vals[1]}")

    def get_degas(self) -> list[int]:
        text = self.query_ascii("DGS")
        vals = _parse_csv_ints(text)
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected DGS response for TPG262: {text!r}")
        return vals

    def set_degas(self, gauge: int, on: bool) -> None:
        _require_channel(gauge, max_channel=2)
        vals = [0, 0]
        vals[gauge - 1] = 1 if on else 0
        self.write_ascii(f"DGS,{vals[0]},{vals[1]}")

    def get_filter(self) -> list[int]:
        text = self.query_ascii("FIL")
        vals = _parse_csv_ints(text)
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected FIL response for TPG262: {text!r}")
        return vals

    def set_filter(self, gauge: int, value: int) -> None:
        _require_channel(gauge, max_channel=2)
        vals = [0, 0]
        vals[gauge - 1] = int(value)
        self.write_ascii(f"FIL,{vals[0]},{vals[1]}")

    def get_calibration(self) -> list[float]:
        text = self.query_ascii("CAL")
        vals = _parse_csv_floats(text)
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected CAL response for TPG262: {text!r}")
        return vals

    def set_calibration(self, gauge: int, value: float) -> None:
        _require_channel(gauge, max_channel=2)
        vals = [1.0, 1.0]
        vals[gauge - 1] = float(value)
        self.write_ascii(f"CAL,{vals[0]:.3f},{vals[1]:.3f}")

    def get_fsr(self) -> list[int]:
        text = self.query_ascii("FSR")
        vals = _parse_csv_ints(text)
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected FSR response for TPG262: {text!r}")
        return vals

    def set_fsr(self, gauge: int, value: int) -> None:
        _require_channel(gauge, max_channel=2)
        vals = [5, 5]
        vals[gauge - 1] = int(value)
        self.write_ascii(f"FSR,{vals[0]},{vals[1]}")

    def get_ofc(self) -> list[int]:
        text = self.query_ascii("OFC")
        vals = _parse_csv_ints(text)
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected OFC response for TPG262: {text!r}")
        return vals

    def set_ofc(self, gauge: int, value: int) -> None:
        _require_channel(gauge, max_channel=2)
        vals = [0, 0]
        vals[gauge - 1] = int(value)
        self.write_ascii(f"OFC,{vals[0]},{vals[1]}")

    def get_ident(self) -> str:
        return self.query_ascii("TID")

    def get_error_status(self) -> str:
        return self.query_ascii("ERR")

    def reset_errors(self) -> str:
        return self.query_ascii("RES,1")

    def get_channel_names(self) -> list[str]:
        # TPG262 hat keine frei konfigurierbaren CID-Namen.
        return ["CH1", "CH2"]

    def set_channel_name(self, gauge: int, name: str) -> None:
        _require_channel(gauge, max_channel=2)
        raise RuntimeError("Channel names are not supported on TPG262.")

    def get_digits(self) -> int:
        raise RuntimeError("Digits parameter is only available on MaxiGauge.")

    def set_digits(self, value: int) -> None:
        raise RuntimeError("Digits parameter is only available on MaxiGauge.")

    def get_contrast(self) -> int:
        raise RuntimeError("Contrast parameter is only available on MaxiGauge.")

    def set_contrast(self, value: int) -> None:
        raise RuntimeError("Contrast parameter is only available on MaxiGauge.")

    def get_screensave(self) -> int:
        raise RuntimeError("Screensave parameter is only available on MaxiGauge.")

    def set_screensave(self, value: int) -> None:
        raise RuntimeError("Screensave parameter is only available on MaxiGauge.")

    def factory_reset(self) -> None:
        self.write_ascii("SAV,0")

    def device_info_lines(self) -> list[str]:
        lines: list[str] = []
        try:
            lines.append(f"TPG262 ident: {self.get_ident()}")
            lines.append(f"TPG262 unit: {self.get_unit()}")
            lines.append(f"TPG262 SEN: {self.get_sensor_onoff()}")
            lines.append(f"TPG262 FIL: {self.get_filter()}")
            lines.append(f"TPG262 CAL: {self.get_calibration()}")
            lines.append(f"TPG262 FSR: {self.get_fsr()}")
            lines.append(f"TPG262 OFC: {self.get_ofc()}")
            lines.append(f"TPG262 DGS: {self.get_degas()}")
            try:
                lines.append(f"TPG262 ERR: {self.get_error_status()}")
            except Exception as exc:
                lines.append(f"TPG262 ERR unavailable: {exc}")
        except Exception as exc:
            lines.append(f"TPG262 diagnostic incomplete: {exc}")
        return lines

    def _read_prx_pair(self) -> tuple[Optional[GaugeReading], Optional[GaugeReading]]:
        text = self._client.query_ascii_response("PRX")
        fields = _split_csv_fields(text)

        # Erwartetes PRX-Format beim Dual-Controller: s1,v1,s2,v2
        if len(fields) >= 4:
            ch1 = _parse_status_value_pair(fields[0], fields[1])
            ch2 = _parse_status_value_pair(fields[2], fields[3])
            return ch1, ch2
        return None, None

    @staticmethod
    def _finalize_reading(
        *,
        reading: Optional[GaugeReading],
        sensor_on: bool,
        fallback: GaugeReading,
    ) -> GaugeReading:
        # Wenn der Sensor logisch "aus" ist, erzwingen wir denselben visuellen Placeholder
        # wie im C++-Original (0.02) und den Status "4 = sensor off".
        if not sensor_on:
            return GaugeReading(status=4, value=0.02)
        if reading is None:
            return fallback
        return reading


@dataclass
class MaxiGaugeDevice:
    """
    Treiber für Pfeiffer MaxiGauge (TPG 256 A, 6 Kanäle).

    Integration in die bestehende Anlage:
    - Wir messen weiterhin zwei Prozesswerte im Plant-State:
      - Chamberdruck
      - Loaddruck
    - Diese beiden Werte werden auf frei konfigurierbare MaxiGauge-Kanäle gemappt.
    """

    transport: SerialDeviceTransport
    settings: SerialSettings
    chamber_channel: int = 1
    load_channel: int = 2
    _client: PfeifferProtocolClient = field(init=False)

    def __post_init__(self) -> None:
        self._client = PfeifferProtocolClient(self.transport, self.settings)
        self.chamber_channel = _clamp_channel_1_to_6(self.chamber_channel)
        self.load_channel = _clamp_channel_1_to_6(self.load_channel)

    def check_connection(self) -> bool:
        reading = self.read_channel(self.chamber_channel)
        return reading is not None

    def query_pressures(self, state: VacuumState) -> tuple[float, float]:
        chamber_fallback = GaugeReading(status=state.p_chamber_status, value=state.p_chamber)
        load_fallback = GaugeReading(status=state.p_load_status, value=state.p_load)

        chamber_reading = self.read_channel(self.chamber_channel)
        load_reading: Optional[GaugeReading]
        if state.single_gauge:
            load_reading = None
        else:
            load_reading = self.read_channel(self.load_channel)

        chamber_final = self._finalize_reading(
            reading=chamber_reading,
            sensor_on=state.chamber_sensor_on,
            fallback=chamber_fallback,
        )
        load_final = self._finalize_reading(
            reading=load_reading,
            sensor_on=state.load_sensor_on,
            fallback=load_fallback,
        )

        state.p_chamber = chamber_final.value
        state.p_load = load_final.value
        state.p_chamber_status = chamber_final.status
        state.p_load_status = load_final.status
        return chamber_final.value, load_final.value

    def set_chamber_sensor(self, on: bool) -> None:
        self.set_sensor_onoff(gauge=self.chamber_channel, turn_on=on)

    def set_load_sensor(self, on: bool) -> None:
        self.set_sensor_onoff(gauge=self.load_channel, turn_on=on)

    def query_ascii(self, command: str) -> str:
        return self._client.query_ascii_response(command)

    def write_ascii(self, command: str) -> None:
        self._client.write_ascii_command(command)

    def read_channel(self, channel: int) -> Optional[GaugeReading]:
        _require_channel(channel, max_channel=6)
        text = self._client.query_ascii_response(f"PR{channel}")
        return _parse_status_value_text(text)

    @staticmethod
    def channel_count() -> int:
        return 6

    def get_unit(self) -> int:
        return int(float(self.query_ascii("UNI")))

    def set_unit(self, unit_code: int) -> None:
        self.write_ascii(f"UNI,{int(unit_code)}")

    def get_sensor_onoff(self) -> list[int]:
        text = self.query_ascii("SEN")
        vals = _parse_csv_ints(text)
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected SEN response for MaxiGauge: {text!r}")
        return vals

    def set_sensor_onoff(self, gauge: int, turn_on: bool) -> None:
        _require_channel(gauge, max_channel=6)
        values = [0, 0, 0, 0, 0, 0]
        values[gauge - 1] = 2 if turn_on else 1
        self.write_ascii("SEN," + ",".join(str(v) for v in values))

    def get_degas(self) -> list[int]:
        text = self.query_ascii("DGS")
        vals = _parse_csv_ints(text)
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected DGS response for MaxiGauge: {text!r}")
        return vals

    def set_degas(self, gauge: int, on: bool) -> None:
        _require_channel(gauge, max_channel=6)
        if gauge not in (4, 5, 6):
            raise RuntimeError("On MaxiGauge, DGS is only valid for channels 4-6.")
        values = [0, 0, 0, 0, 0, 0]
        values[gauge - 1] = 1 if on else 0
        self.write_ascii("DGS," + ",".join(str(v) for v in values))

    def get_filter(self) -> list[int]:
        text = self.query_ascii("FIL")
        vals = _parse_csv_ints(text)
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected FIL response for MaxiGauge: {text!r}")
        return vals

    def set_filter(self, gauge: int, value: int) -> None:
        _require_channel(gauge, max_channel=6)
        values = [0, 0, 0, 0, 0, 0]
        values[gauge - 1] = int(value)
        self.write_ascii("FIL," + ",".join(str(v) for v in values))

    def get_calibration(self, gauge: int) -> float:
        _require_channel(gauge, max_channel=6)
        return float(self.query_ascii(f"CA{gauge}"))

    def set_calibration(self, gauge: int, value: float) -> None:
        _require_channel(gauge, max_channel=6)
        self.write_ascii(f"CA{gauge},{float(value):.3f}")

    def get_fsr(self) -> list[int]:
        text = self.query_ascii("FSR")
        vals = _parse_csv_ints(text)
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected FSR response for MaxiGauge: {text!r}")
        return vals

    def set_fsr(self, gauge: int, value: int) -> None:
        _require_channel(gauge, max_channel=6)
        values = [3, 3, 3, 3, 3, 3]
        values[gauge - 1] = int(value)
        self.write_ascii("FSR," + ",".join(str(v) for v in values))

    def get_ofc(self) -> list[int]:
        text = self.query_ascii("OFC")
        vals = _parse_csv_ints(text)
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected OFC response for MaxiGauge: {text!r}")
        return vals

    def set_ofc(self, gauge: int, value: int) -> None:
        _require_channel(gauge, max_channel=6)
        values = [0, 0, 0, 0, 0, 0]
        values[gauge - 1] = int(value)
        self.write_ascii("OFC," + ",".join(str(v) for v in values))

    def get_ident(self) -> str:
        # Manche MaxiGauge-Modelle kennen TID, andere liefern nur CID.
        try:
            return self.query_ascii("TID")
        except Exception:
            return "CID=" + ",".join(self.get_channel_names())

    def get_channel_names(self) -> list[str]:
        text = self.query_ascii("CID")
        vals = _split_csv_fields(text)
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected CID response for MaxiGauge: {text!r}")
        return vals

    def set_channel_name(self, gauge: int, name: str) -> None:
        _require_channel(gauge, max_channel=6)
        sanitized = "".join(ch for ch in str(name).upper() if ch.isalnum())[:4].ljust(4)
        current = self.get_channel_names()
        current[gauge - 1] = sanitized
        self.write_ascii("CID," + ",".join(current))

    def get_digits(self) -> int:
        return int(float(self.query_ascii("DCD")))

    def set_digits(self, value: int) -> None:
        self.write_ascii(f"DCD,{int(value)}")

    def get_contrast(self) -> int:
        return int(float(self.query_ascii("DCC")))

    def set_contrast(self, value: int) -> None:
        self.write_ascii(f"DCC,{int(value)}")

    def get_screensave(self) -> int:
        return int(float(self.query_ascii("DCS")))

    def set_screensave(self, value: int) -> None:
        self.write_ascii(f"DCS,{int(value)}")

    def factory_reset(self) -> None:
        self.write_ascii("SAV,1")

    def device_info_lines(self) -> list[str]:
        lines: list[str] = []
        try:
            lines.append(f"MaxiGauge ident: {self.get_ident()}")
            lines.append(f"MaxiGauge unit: {self.get_unit()}")
            lines.append(f"MaxiGauge SEN: {self.get_sensor_onoff()}")
            lines.append(f"MaxiGauge FIL: {self.get_filter()}")
            lines.append(f"MaxiGauge OFC: {self.get_ofc()}")
            lines.append(f"MaxiGauge FSR: {self.get_fsr()}")
            lines.append(f"MaxiGauge DGS: {self.get_degas()}")
            lines.append(f"MaxiGauge names: {self.get_channel_names()}")
            lines.append(f"MaxiGauge digits: {self.get_digits()}")
            lines.append(f"MaxiGauge contrast: {self.get_contrast()}")
            lines.append(f"MaxiGauge screensave: {self.get_screensave()}")
        except Exception as exc:
            lines.append(f"MaxiGauge diagnostic incomplete: {exc}")
        return lines

    @staticmethod
    def _finalize_reading(
        *,
        reading: Optional[GaugeReading],
        sensor_on: bool,
        fallback: GaugeReading,
    ) -> GaugeReading:
        if not sensor_on:
            return GaugeReading(status=4, value=0.02)
        if reading is None:
            return fallback
        return reading


@dataclass
class DualGaugeDevice(TPG262GaugeDevice):
    """
    Rückwärtskompatibler Alias.

    Hintergrund:
    Der restliche Code importiert historisch `DualGaugeDevice`.
    Damit bestehende Imports stabil bleiben, erbt der Name direkt vom
    neuen, robusten `TPG262GaugeDevice`.
    """


def _normalize_ascii_payload(raw: bytes) -> str:
    # Wir dekodieren bewusst tolerant, weil einige Controller zusätzliche
    # Steuerzeichen oder CR/LF-Varianten senden.
    return raw.decode("ascii", errors="ignore").strip()


def _split_csv_fields(text: str) -> list[str]:
    # Einige Geräte/Versionen nutzen ';' statt ',' als Trennzeichen.
    normalized = text.replace(";", ",")
    return [field.strip() for field in normalized.split(",")]


def _parse_status_value_text(text: str) -> Optional[GaugeReading]:
    fields = _split_csv_fields(text)
    if len(fields) < 2:
        return None
    return _parse_status_value_pair(fields[0], fields[1])


def _parse_status_value_pair(status_text: str, value_text: str) -> Optional[GaugeReading]:
    try:
        # Status wird als int erwartet; float-Zwischenschritt macht das Parsing robuster
        # gegen exotische Antworten wie "0.0".
        status = int(float(status_text))
        value = float(value_text)
    except ValueError:
        return None
    return GaugeReading(status=status, value=value)


def _parse_csv_ints(text: str) -> list[int]:
    values: list[int] = []
    for raw in _split_csv_fields(text):
        if raw == "":
            continue
        values.append(int(float(raw)))
    return values


def _parse_csv_floats(text: str) -> list[float]:
    values: list[float] = []
    for raw in _split_csv_fields(text):
        if raw == "":
            continue
        values.append(float(raw))
    return values


def _require_channel(channel: int, *, max_channel: int) -> None:
    if not (1 <= int(channel) <= int(max_channel)):
        raise ValueError(f"Channel must be in range 1..{max_channel}, got {channel!r}")


def _clamp_channel_1_to_6(value: int) -> int:
    return max(1, min(6, int(value)))
