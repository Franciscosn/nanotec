from __future__ import annotations

import unittest

from sputtering_app import protocols
from sputtering_app.devices.pinnacle import PinnacleDevice, PinnacleProtocolError, PinnacleSafetyError
from sputtering_app.devices.transport import SerialSettings
from sputtering_app.models import PinnacleChannelState, RegulationMode


class _FakePinnacleTransport:
    """
    Sehr einfacher Test-Transport fuer Pinnacle.

    Verhalten:
    - Jede `query(...)`-Anfrage speichert das gesendete Request-Frame.
    - Die Antwort wird aus einer vorbereiteten Queue geliefert.
    """

    def __init__(self) -> None:
        self.sent_requests: list[bytes] = []
        self._responses: list[bytes] = []

    def queue_response(self, response: bytes) -> None:
        self._responses.append(response)

    def query(
        self,
        settings: SerialSettings,
        payload: bytes,
        *,
        read_size: int = 256,
        delay_after_write: float = 0.05,
    ) -> bytes:
        self.sent_requests.append(payload)
        if not self._responses:
            raise AssertionError("No fake Pinnacle response queued")
        return self._responses.pop(0)


def _resp(address: int, command_name: str, payload: bytes) -> bytes:
    """Baut eine gueltige Fake-Antwort fuer ein gegebenes Kommando."""

    cmd_id, _ = protocols.PINNACLE_CMD[command_name]
    return protocols.pinnacle_frame(address, cmd_id, payload)


def _request_address(frame: bytes) -> int:
    """
    Extrahiert die logische Adresse aus einem Request-Frame.

    Frame-Regel:
    - byte0 = address + payload_len
    - byte2 = 2 + payload_len
    """

    payload_len = int(frame[2]) - 2
    return (int(frame[0]) - payload_len) & 0xFF


class PinnacleDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = _FakePinnacleTransport()
        self.settings = SerialSettings(port="COM_TEST", baudrate=9600, parity="O", bytesize=8, stopbits=1, timeout=0.5)
        self.device = PinnacleDevice(self.transport, self.settings)  # type: ignore[arg-type]

    def test_read_channel_decodes_all_fields(self) -> None:
        # Reihenfolge muss zur Implementierung in read_channel passen.
        self.transport.queue_response(_resp(8, "REQ_ACTUAL_VOLTAGE", b"\xD2\x04"))  # 1234 V
        self.transport.queue_response(_resp(8, "REQ_ACTUAL_CURRENT", b"\x1E\x00"))  # 30 A
        self.transport.queue_response(_resp(8, "REQ_ACTUAL_POWER", b"\x2C\x01"))  # 300 -> 0.300 W
        self.transport.queue_response(_resp(8, "REQ_PULSE_FREQ_INDEX", b"\x0A"))  # 10 -> 50 kHz
        self.transport.queue_response(_resp(8, "REQ_PULSE_REVERSE_TIME", b"\x14"))  # 20 -> 2.0 us
        self.transport.queue_response(_resp(8, "REQ_SETPOINT", b"\x7B\x00\x08"))  # 123, mode=current

        channel = PinnacleChannelState(address=8)
        self.device.read_channel(channel)

        self.assertAlmostEqual(channel.voltage, 1234.0)
        self.assertAlmostEqual(channel.current, 30.0)
        self.assertAlmostEqual(channel.power, 0.300, places=6)
        self.assertEqual(channel.pulse_frequency_index, 10)
        self.assertEqual(channel.act_pulse_frequency, 50)
        self.assertEqual(channel.pulse_reverse_index, 20)
        self.assertAlmostEqual(channel.act_pulse_reverse_time, 2.0, places=6)
        self.assertEqual(channel.mode, RegulationMode.CURRENT)
        self.assertAlmostEqual(channel.setpoint_actual, 1.23, places=6)

    def test_read_channel_rejects_crc_error(self) -> None:
        good = _resp(8, "REQ_ACTUAL_VOLTAGE", b"\x01\x00")
        bad = good[:-1] + bytes([good[-1] ^ 0xFF])

        self.transport.queue_response(bad)
        channel = PinnacleChannelState(address=8)

        with self.assertRaises(PinnacleProtocolError):
            self.device.read_channel(channel)

    def test_apply_channel_control_uses_channel_address_for_all_requests(self) -> None:
        channel = PinnacleChannelState(
            address=148,
            active=False,
            mode=RegulationMode.VOLTAGE,
            pulse_frequency_index=9,
            pulse_reverse_index=12,
            setpoint=450.0,
        )

        # Schreibantworten.
        self.transport.queue_response(_resp(148, "PULSE_REVERSE_TIME", b""))
        self.transport.queue_response(_resp(148, "PULSE_FREQ_INDEX", b""))
        self.transport.queue_response(_resp(148, "REG_METHOD", b""))
        self.transport.queue_response(_resp(148, "SETPOINT", b""))
        self.transport.queue_response(_resp(148, "DC_OFF", b""))

        # Readback-Verifikation.
        self.transport.queue_response(_resp(148, "REQ_PULSE_FREQ_INDEX", b"\x09"))
        self.transport.queue_response(_resp(148, "REQ_PULSE_REVERSE_TIME", b"\x0C"))
        self.transport.queue_response(_resp(148, "REQ_SETPOINT", b"\xC2\x01\x07"))  # 450, Voltage(7)

        self.device.apply_channel_control(channel)

        sent_cmd_ids = [int(frame[1]) for frame in self.transport.sent_requests]
        self.assertEqual(sent_cmd_ids, [93, 92, 3, 6, 1, 146, 147, 164])

        for frame in self.transport.sent_requests:
            self.assertEqual(_request_address(frame), 148)

    def test_apply_channel_control_attempts_emergency_off_on_failure(self) -> None:
        channel = PinnacleChannelState(
            address=8,
            active=True,
            mode=RegulationMode.CURRENT,
            pulse_frequency_index=2,
            pulse_reverse_index=3,
            setpoint=0.75,
        )

        self.transport.queue_response(_resp(8, "PULSE_REVERSE_TIME", b""))
        self.transport.queue_response(_resp(8, "PULSE_FREQ_INDEX", b""))

        # Fehlerhafte REG_METHOD-Antwort (CRC absichtlich kaputt).
        bad = _resp(8, "REG_METHOD", b"")
        self.transport.queue_response(bad[:-1] + bytes([bad[-1] ^ 0xAA]))

        # Emergency-Off-Antwort.
        self.transport.queue_response(_resp(8, "DC_OFF", b""))

        with self.assertRaises(PinnacleSafetyError):
            self.device.apply_channel_control(channel)

        sent_cmd_ids = [int(frame[1]) for frame in self.transport.sent_requests]
        self.assertIn(1, sent_cmd_ids)  # DC_OFF wurde versucht.

    def test_check_connection_returns_false_when_response_is_invalid(self) -> None:
        self.transport.queue_response(b"\x01")
        self.assertFalse(self.device.check_connection())


if __name__ == "__main__":
    unittest.main()
