from __future__ import annotations

"""
Pinnacle-MDX Treiber (Python Rewrite).

Wichtiger Sicherheitskontext:
- Dieses Modul bedient ein Hochspannungsgeraet.
- Kommunikationsfehler duerfen nicht stillschweigend ignoriert werden.
- Antworten werden deshalb strikt validiert (Frame-Laenge, Adresse,
  Kommandocode und CRC).

Hinweis zur Lesbarkeit:
- Die Kommentare sind bewusst sehr ausfuehrlich geschrieben, damit auch
  Einsteiger im Labor den Ablauf nachvollziehen koennen.
"""

import time
from dataclasses import dataclass

from .. import protocols
from ..models import MODE_TO_REGULATION_CODE, REGULATION_CODE_TO_MODE, PinnacleChannelState, RegulationMode
from .transport import SerialDeviceTransport, SerialSettings


class PinnacleProtocolError(RuntimeError):
    """Fehlerklasse fuer ungültige/unerwartete Pinnacle-Protokollantworten."""


class PinnacleSafetyError(RuntimeError):
    """
    Fehlerklasse fuer sicherheitsrelevante Bedienpfade.

    Diese Exception nutzen wir, wenn ein Apply-Schritt fehlschlaegt und wir den
    Fehler klar als "bedienkritisch" kennzeichnen wollen.
    """


@dataclass(frozen=True)
class _PinnacleControlTarget:
    """
    Internes Sollbild fuer einen Kanal.

    Warum ein separates Objekt?
    - Wir normalisieren (clampen/skalieren) alle Eingabewerte genau einmal.
    - Dieselben normierten Werte werden sowohl zum Senden als auch zur
      Readback-Verifikation genutzt.
    """

    address: int
    active: bool
    mode: RegulationMode
    mode_code: int
    pulse_frequency_index: int
    pulse_reverse_index: int
    setpoint_raw: int


@dataclass(frozen=True)
class _PinnacleControlSnapshot:
    """
    Internes Readback-Bild eines Kanals direkt vom Geraet.

    Das Snapshot-Bild wird nach dem Schreiben gelesen und mit dem Sollbild
    verglichen, um Fehlparametrierungen frueh zu erkennen.
    """

    pulse_frequency_index: int
    pulse_reverse_index: int
    mode_code: int
    setpoint_raw: int


@dataclass
class PinnacleDevice:
    """
    Geraeteklasse fuer Pinnacle MDX/Pinnacle Plus.

    Kernfunktionen:
    1) Lesen der Istwerte pro Kanal.
    2) Schreiben der Sollwerte pro Kanal.
    3) Strikte Protokollvalidierung jeder Antwort.
    4) Readback-Verifikation nach kritischen Schreibvorgaengen.

    Architekturentscheidung:
    - Wir halten das Protokollwissen in dieser Klasse zusammen,
      damit Controller/GUI mit fachlichen Werten arbeiten koennen,
      ohne Binary-Details kennen zu muessen.
    """

    transport: SerialDeviceTransport
    settings: SerialSettings
    response_read_size: int = 64
    command_delay_s: float = 0.05
    strict_protocol: bool = True
    query_retries: int = 0
    retry_backoff_s: float = 0.03

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------
    def set_runtime_options(
        self,
        *,
        strict_protocol: bool | None = None,
        query_retries: int | None = None,
        command_delay_s: float | None = None,
        response_read_size: int | None = None,
    ) -> None:
        if strict_protocol is not None:
            # True  -> strenges Header/CMD/CRC-Checking.
            # False -> erlaubt lenientes Fallback-Decoding.
            self.strict_protocol = bool(strict_protocol)
        if query_retries is not None:
            # Anzahl Zusatzversuche nach dem ersten Versuch.
            self.query_retries = max(0, int(query_retries))
        if command_delay_s is not None:
            # Kleine Wartezeit nach Write, bevor Read startet.
            self.command_delay_s = max(0.0, float(command_delay_s))
        if response_read_size is not None:
            # Untergrenze 8 Bytes verhindert unbrauchbar kleine Reads.
            self.response_read_size = max(8, int(response_read_size))

    def check_connection(
        self,
        addresses: tuple[int, ...] | None = None,
        *,
        require_all: bool = False,
    ) -> bool:
        """
        Schneller Verbindungstest fuer die Initialisierung.

        Vorgehen:
        - Wir fragen die aktuelle Spannung von Kanaladresse 8 ab.
        - Nur wenn die Antwort vollstaendig und CRC-valide ist, gilt der Check als ok.

        Warum diese Strenge?
        - Bei Hochspannung ist ein "falsch positives" Verbundenheits-Signal
          riskant. Lieber konservativ als stillschweigend unsicher.
        """

        probe_addresses = addresses or (8,)
        results: list[bool] = []
        for address in probe_addresses:
            try:
                # Ein valides Spannungs-Read reicht als "Adresse lebt"-Signal.
                self._query_u16(int(address) & 0xFF, "REQ_ACTUAL_VOLTAGE")
                results.append(True)
            except Exception:
                # Fehler wird hier nur als "False" gespeichert; Details bleiben
                # fuer den regulaeren Tick-/Logpfad.
                results.append(False)
        return all(results) if require_all else any(results)

    def ping_address(self, address: int) -> bool:
        try:
            # Lightweight-Ping ueber denselben Spannungsquery wie beim Connect-Test.
            self._query_u16(int(address) & 0xFF, "REQ_ACTUAL_VOLTAGE")
            return True
        except Exception:
            return False

    def force_output_off(self, address: int) -> bool:
        try:
            # Direkter OFF-Befehl ohne vorheriges Readback.
            self._send_command(int(address) & 0xFF, "DC_OFF")
            return True
        except Exception:
            return False

    def read_channel(self, channel: PinnacleChannelState) -> None:
        """
        Liest einen kompletten Istwertsatz fuer genau einen Kanal.

        Gelesene Daten:
        - Voltage, Current, Power
        - Pulse-Frequency-Index, Pulse-Reverse-Index
        - Setpoint-Readback + Regulation-Mode-Code
        """

        # Die Reihenfolge ist inhaltlich egal, aber bewusst stabil gehalten.
        voltage = self._query_u16(channel.address, "REQ_ACTUAL_VOLTAGE")
        current = self._query_u16(channel.address, "REQ_ACTUAL_CURRENT")
        power_raw = self._query_u16(channel.address, "REQ_ACTUAL_POWER")
        freq_idx = self._query_u8(channel.address, "REQ_PULSE_FREQ_INDEX")
        reverse_idx = self._query_u8(channel.address, "REQ_PULSE_REVERSE_TIME")
        # REQ_SETPOINT liefert 3 Byte:
        # - Byte0/1: Setpoint (little-endian 16 bit)
        # - Byte2:   Regulation-Mode-Code
        setpoint_payload = self._send_command(
            channel.address,
            "REQ_SETPOINT",
            expected_response_payload_len=3,
        )

        # Legacy-C++-Skalierungen werden beibehalten:
        # - Power kommt in mW-aehnlicher Skalierung und wird mit 0.001 umgerechnet.
        # - Current-Setpoint wird bei Regelmodus "Current" durch Faktor 0.01 korrigiert.
        channel.voltage = float(voltage)
        channel.current = float(current)
        channel.power = float(power_raw) * 0.001
        channel.act_pulse_frequency = int(freq_idx) * 5
        channel.pulse_frequency_index = int(freq_idx)
        channel.act_pulse_reverse_time = float(reverse_idx) * 0.1
        channel.pulse_reverse_index = int(reverse_idx)

        setpoint_raw = int.from_bytes(setpoint_payload[0:2], byteorder="little", signed=False)
        mode_code = int(setpoint_payload[2])
        channel.act_regulation_mode_code = mode_code
        # Unbekannte Codes werden explizit als "Fehler" markiert.
        channel.mode = REGULATION_CODE_TO_MODE.get(mode_code, RegulationMode.FEHLER)
        channel.regulation = channel.mode.value

        setpoint_actual = float(setpoint_raw)
        if channel.mode == RegulationMode.CURRENT:
            # Rueckskalierung passend zur Legacy-Kodierung x100.
            setpoint_actual *= 0.01
        channel.setpoint_actual = setpoint_actual

    def apply_channel_control(self, channel: PinnacleChannelState, *, verify_after_apply: bool = True) -> None:
        """
        Schreibt die Sollwerte fuer einen Kanal in sicherer Reihenfolge.

        Reihenfolge wie im Legacy-System:
        1) Pulse Reverse
        2) Pulse Frequency
        3) Regulation Mode
        4) Setpoint
        5) Output ON/OFF

        Sicherheitszusaetze im Rewrite:
        - Strikte Antwortvalidierung fuer jeden Schritt.
        - Optionales Readback-Verify nach dem Schreiben.
        - Bei Fehler waehrend "active=True" wird fail-safe `DC_OFF` versucht.
        """

        target = self._normalized_target(channel)

        try:
            # Schreibreihenfolge wie im Legacy-Code beibehalten.
            # Das minimiert Migrationsrisiko fuer bestehende Arbeitsablaeufe.
            self._send_command(target.address, "PULSE_REVERSE_TIME", bytes([target.pulse_reverse_index]))
            self._send_command(target.address, "PULSE_FREQ_INDEX", bytes([target.pulse_frequency_index]))
            self._send_command(target.address, "REG_METHOD", bytes([target.mode_code]))
            self._send_command(target.address, "SETPOINT", target.setpoint_raw.to_bytes(2, byteorder="little"))
            self._send_command(target.address, "DC_ON" if target.active else "DC_OFF")

            if verify_after_apply:
                # Verifikation liest den Geraetestand nochmal und vergleicht
                # Soll vs. Ist feldweise.
                snapshot = self._read_control_snapshot(target.address)
                self._ensure_snapshot_matches_target(target, snapshot)
        except Exception as exc:
            # Fail-safe-Idee:
            # Wenn der Benutzer eigentlich "aktiv" wollte und mitten im Write-Pfad
            # ein Fehler auftritt, versuchen wir aktiv ein DC_OFF auf dem Kanal.
            if target.active:
                self._try_emergency_off(target.address)
            raise PinnacleSafetyError(
                f"Pinnacle apply failed on address {target.address}: {exc}"
            ) from exc

    def apply_controls(self, *channels: PinnacleChannelState) -> None:
        """
        Schreibt mehrere Kanaele nacheinander.

        Warum *channels statt fest A/B?
        - Das ist die technische Basis fuer kuenftige Multi-Head-Erweiterungen,
          ohne die API nochmal komplett umzubauen.
        - Der aktuelle PlantState nutzt weiterhin A/B, bleibt also kompatibel.
        """

        for channel in channels:
            self.apply_channel_control(channel)

    # ------------------------------------------------------------------
    # Interne Hilfen: Sollbild / Readback / Verifikation
    # ------------------------------------------------------------------
    def _normalized_target(self, channel: PinnacleChannelState) -> _PinnacleControlTarget:
        """
        Ueberfuehrt den Kanalzustand in ein normiertes, sende-faehiges Sollbild.

        Normierungen:
        - Indizes werden auf 0..255 begrenzt.
        - Setpoint wird je nach Modus korrekt skaliert und auf 16 Bit begrenzt.
        """

        # Protokollfeld fuer diese Werte ist je 1 Byte -> zulaessig 0..255.
        pulse_reverse_index = max(0, min(255, int(channel.pulse_reverse_index)))
        pulse_frequency_index = max(0, min(255, int(channel.pulse_frequency_index)))

        # Sicherheitsfallback: ungueltiger Modus => CURRENT.
        mode = channel.mode if isinstance(channel.mode, RegulationMode) else RegulationMode.CURRENT
        mode_code = MODE_TO_REGULATION_CODE.get(mode, 8)

        # Negative Sollwerte sind fachlich ungueltig und werden auf 0 geklemmt.
        setpoint_value = max(0.0, float(channel.setpoint))
        if mode == RegulationMode.CURRENT:
            # Legacy-Konvention: Current-Setpoint wird mit Faktor 100 kodiert.
            setpoint_raw = int(round(setpoint_value * 100.0))
        else:
            setpoint_raw = int(round(setpoint_value))
        # Protokollfeld ist uint16 -> 0..65535.
        setpoint_raw = max(0, min(65535, setpoint_raw))

        return _PinnacleControlTarget(
            # Adresse als Byte normalisieren.
            address=int(channel.address) & 0xFF,
            active=bool(channel.active),
            mode=mode,
            mode_code=mode_code,
            pulse_frequency_index=pulse_frequency_index,
            pulse_reverse_index=pulse_reverse_index,
            setpoint_raw=setpoint_raw,
        )

    def _read_control_snapshot(self, address: int) -> _PinnacleControlSnapshot:
        """
        Liest die wichtigsten Regelparameter direkt vom Geraet zur Verifikation.
        """

        freq_idx = self._query_u8(address, "REQ_PULSE_FREQ_INDEX")
        reverse_idx = self._query_u8(address, "REQ_PULSE_REVERSE_TIME")
        setpoint_payload = self._send_command(
            address,
            "REQ_SETPOINT",
            expected_response_payload_len=3,
        )

        setpoint_raw = int.from_bytes(setpoint_payload[0:2], byteorder="little", signed=False)
        mode_code = int(setpoint_payload[2])

        return _PinnacleControlSnapshot(
            pulse_frequency_index=freq_idx,
            pulse_reverse_index=reverse_idx,
            mode_code=mode_code,
            setpoint_raw=setpoint_raw,
        )

    @staticmethod
    def _ensure_snapshot_matches_target(target: _PinnacleControlTarget, snapshot: _PinnacleControlSnapshot) -> None:
        """
        Vergleicht Sollbild und Readback-Snapshot.

        Bei Abweichung wird eine klare Exception geworfen, damit der Controller
        den Portzustand als fehlgeschlagen markieren kann.
        """

        mismatches: list[str] = []

        if snapshot.pulse_frequency_index != target.pulse_frequency_index:
            mismatches.append(
                "pulse_frequency_index "
                f"expected={target.pulse_frequency_index} got={snapshot.pulse_frequency_index}"
            )

        if snapshot.pulse_reverse_index != target.pulse_reverse_index:
            mismatches.append(
                "pulse_reverse_index "
                f"expected={target.pulse_reverse_index} got={snapshot.pulse_reverse_index}"
            )

        if snapshot.mode_code != target.mode_code:
            mismatches.append(f"mode_code expected={target.mode_code} got={snapshot.mode_code}")

        if snapshot.setpoint_raw != target.setpoint_raw:
            mismatches.append(f"setpoint_raw expected={target.setpoint_raw} got={snapshot.setpoint_raw}")

        if mismatches:
            details = "; ".join(mismatches)
            raise PinnacleProtocolError(
                f"pinnacle readback verification failed on address {target.address}: {details}"
            )

    def _try_emergency_off(self, address: int) -> None:
        """
        Fail-safe-Hilfsfunktion: versucht ein `DC_OFF` fuer den angegebenen Kanal.

        Diese Funktion wirft absichtlich keine neue Exception nach oben,
        weil wir den urspruenglichen Fehler nicht ueberdecken wollen.
        """

        try:
            self._send_command(address, "DC_OFF")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Interne Hilfen: Query / Frame-Validierung
    # ------------------------------------------------------------------
    def _query_u16(self, address: int, command_name: str) -> int:
        payload = self._send_command(address, command_name, expected_response_payload_len=2)
        return int.from_bytes(payload, byteorder="little", signed=False)

    def _query_u8(self, address: int, command_name: str) -> int:
        payload = self._send_command(address, command_name, expected_response_payload_len=1)
        return int(payload[0])

    def _send_command(
        self,
        address: int,
        command_name: str,
        payload: bytes = b"",
        *,
        expected_response_payload_len: int | None = None,
    ) -> bytes:
        """
        Sendet genau ein Pinnacle-Kommando und validiert die Antwort.

        Request-Validierung:
        - Payload-Laenge muss zur Kommando-Definition passen.

        Response-Validierung:
        - Frame muss lang genug sein.
        - Length-Byte muss konsistent sein.
        - Adresse und Kommando-Byte muessen zum Request passen.
        - CRC muss stimmen.
        - Optional: Payload-Laenge muss einem erwarteten Wert entsprechen.
        """

        # Kommando-Metadaten aus zentraler Tabelle:
        # - cmd_id: Kommando-Byte
        # - expected_payload_len: erlaubte Request-Payload-Laenge
        cmd_id, expected_payload_len = protocols.PINNACLE_CMD[command_name]
        if len(payload) != expected_payload_len:
            raise ValueError(
                f"Pinnacle command {command_name} expects {expected_payload_len} bytes, got {len(payload)}"
            )

        # Rohframe in Legacy-kompatiblem Format bauen.
        frame = protocols.pinnacle_frame(address, cmd_id, payload)
        # attempts = 1 + retries, aber mindestens 1.
        attempts = max(1, int(self.query_retries) + 1)
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                # Query = write + kurze Pause + read.
                raw = self.transport.query(
                    self.settings,
                    frame,
                    read_size=self.response_read_size,
                    delay_after_write=self.command_delay_s,
                )
                # Im strikten Modus muss die Antwort voll valides Pinnacle-Frame sein.
                return self._decode_response(
                    raw,
                    expected_address=address,
                    expected_cmd_id=cmd_id,
                    expected_payload_len=expected_response_payload_len,
                    command_name=command_name,
                )
            except PinnacleProtocolError as exc:
                last_error = exc
                if not self.strict_protocol:
                    try:
                        # Lenient-Fallback nur explizit erlaubt, nie stillschweigend.
                        return self._decode_response_lenient(
                            raw if "raw" in locals() else b"",
                            expected_payload_len=expected_response_payload_len,
                            command_name=command_name,
                        )
                    except Exception as lenient_exc:
                        last_error = lenient_exc
            except Exception as exc:
                # Transport-/Timeout-/sonstige Fehler werden ebenfalls gemerkt.
                last_error = exc

            if attempt + 1 < attempts:
                # Kurzes Backoff vor naechstem Versuch.
                time.sleep(max(0.0, float(self.retry_backoff_s)))

        if last_error is not None:
            raise last_error
        raise PinnacleProtocolError(f"{command_name}: unknown pinnacle send error")

    @staticmethod
    def _decode_response(
        raw: bytes,
        *,
        expected_address: int,
        expected_cmd_id: int,
        expected_payload_len: int | None,
        command_name: str,
    ) -> bytes:
        """
        Dekodiert und validiert einen Pinnacle-Antwortframe.

        Erwartetes Frameformat (wie im Legacy `prepare_PiNcmd`):
        - byte0: address + payload_len
        - byte1: command id
        - byte2: 2 + payload_len
        - byte3..: payload
        - letztes Byte: XOR-CRC ueber alle vorherigen Bytes
        """

        if not raw:
            raise PinnacleProtocolError(f"{command_name}: empty response")

        if len(raw) < 4:
            raise PinnacleProtocolError(
                f"{command_name}: response too short ({len(raw)} bytes): {PinnacleDevice._hex(raw)}"
            )

        # Byte2 enthaelt laut Legacy-Protokoll: (2 + payload_len).
        declared_length = int(raw[2])
        if declared_length < 2:
            raise PinnacleProtocolError(
                f"{command_name}: invalid length byte={declared_length}: {PinnacleDevice._hex(raw)}"
            )

        # Tatsächliche Frame-Laenge ist "declared_length + 2".
        frame_length = declared_length + 2
        if len(raw) < frame_length:
            raise PinnacleProtocolError(
                f"{command_name}: incomplete frame, expected {frame_length} bytes, got {len(raw)}: "
                f"{PinnacleDevice._hex(raw)}"
            )

        # Alles hinter dem ersten vollstaendigen Frame wird ignoriert.
        frame = raw[:frame_length]
        payload_len = declared_length - 2

        # byte0 muss mit "address + payload_len" konsistent sein.
        expected_first = (int(expected_address) + payload_len) & 0xFF
        if int(frame[0]) != expected_first:
            raise PinnacleProtocolError(
                f"{command_name}: address/length mismatch in response byte0, expected {expected_first}, got {int(frame[0])}; "
                f"frame={PinnacleDevice._hex(frame)}"
            )

        # byte1 muss das angefragte Kommando spiegeln.
        if int(frame[1]) != int(expected_cmd_id):
            raise PinnacleProtocolError(
                f"{command_name}: command echo mismatch, expected {expected_cmd_id}, got {int(frame[1])}; "
                f"frame={PinnacleDevice._hex(frame)}"
            )

        # Optionaler Guard fuer exakt erwartete Nutzdatenlaenge.
        if expected_payload_len is not None and payload_len != expected_payload_len:
            raise PinnacleProtocolError(
                f"{command_name}: payload length mismatch, expected {expected_payload_len}, got {payload_len}; "
                f"frame={PinnacleDevice._hex(frame)}"
            )

        # XOR-CRC ueber alle Bytes ausser letztem CRC-Byte.
        crc = 0
        for b in frame[:-1]:
            crc ^= int(b)
        if int(frame[-1]) != crc:
            raise PinnacleProtocolError(
                f"{command_name}: CRC mismatch, expected {crc}, got {int(frame[-1])}; frame={PinnacleDevice._hex(frame)}"
            )

        # Payload beginnt bei Byte3 und hat payload_len Bytes.
        return bytes(frame[3 : 3 + payload_len])

    @staticmethod
    def _decode_response_lenient(
        raw: bytes,
        *,
        expected_payload_len: int | None,
        command_name: str,
    ) -> bytes:
        """
        Lenient-Decoding fuer inhomogene Feldgeraete/Adapter.

        Verhalten:
        - Versucht bei inkonsistenten Headern trotzdem Nutzdaten zu extrahieren.
        - Ignoriert Adresse/CMD/CRC-Pruefung bewusst.
        - Wird nur genutzt, wenn `strict_protocol=False`.
        """

        if not raw:
            raise PinnacleProtocolError(f"{command_name}: empty response (lenient)")
        if len(raw) < 4:
            raise PinnacleProtocolError(f"{command_name}: response too short (lenient): {PinnacleDevice._hex(raw)}")

        declared_length = int(raw[2]) if len(raw) >= 3 else 0
        frame_length = declared_length + 2 if declared_length >= 2 else len(raw)
        # Laenge robust auf den empfangenen Puffer einklemmen.
        frame_length = max(4, min(frame_length, len(raw)))
        frame = raw[:frame_length]

        # Lenient-Interpretation: Header ignorieren, Byte3..-2 als Nutzdaten.
        payload = bytes(frame[3:-1]) if len(frame) >= 5 else b""
        if expected_payload_len is None:
            return payload

        if len(payload) >= expected_payload_len:
            return payload[:expected_payload_len]
        # Falls zu kurz, mit Nullbytes auffuellen.
        return payload + (b"\x00" * (expected_payload_len - len(payload)))

    @staticmethod
    def _hex(data: bytes) -> str:
        """Kleine Hilfsfunktion fuer lesbare Hex-Ausgaben in Fehlermeldungen."""

        if not data:
            return "<empty>"
        return data.hex(" ")
