from __future__ import annotations

import unittest

from sputtering_app.devices.dualg import MaxiGaugeDevice, TPG262GaugeDevice
from sputtering_app.devices.transport import ExchangeStep, SerialSettings
from sputtering_app.models import VacuumState


class _FakePfeifferTransport:
    """
    Sehr einfacher Test-Transport für die Pfeiffer-Treiber.

    Ziel:
    - Keine echte serielle Hardware notwendig.
    - Wir können exakt vorgeben, welche ACK-/Datenantwort ein Kommando liefert.
    - Wir können prüfen, welche Bytes der Treiber tatsächlich gesendet hat.
    """

    def __init__(self) -> None:
        self._responses: dict[bytes, list[bytes]] = {}
        self.sent_payloads: list[bytes] = []

    def add_response(self, command_payload: bytes, ack: bytes, data: bytes | None = None) -> None:
        if data is None:
            self._responses[command_payload] = [ack]
            return
        self._responses[command_payload] = [ack, data]

    def exchange(self, settings: SerialSettings, steps: list[ExchangeStep]) -> list[bytes]:
        if not steps:
            raise AssertionError("Test transport received empty exchange steps.")

        first_payload = steps[0].payload
        self.sent_payloads.append(first_payload)
        response = self._responses.get(first_payload)
        if response is None:
            raise AssertionError(f"No fake response configured for payload {first_payload!r}")
        return response


class TPG262DeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SerialSettings(port="COM_TEST", baudrate=9600, timeout=0.5)
        self.transport = _FakePfeifferTransport()
        self.device = TPG262GaugeDevice(self.transport, self.settings)  # type: ignore[arg-type]

    def test_pr1_uses_value_field_not_status_field(self) -> None:
        """
        Regression-Test für den zuvor gefundenen Fehler:
        Bei PR1 war versehentlich der Status (erstes Feld) als Druckwert interpretiert worden.
        """

        self.transport.add_response(b"PR1\r", ack=b"\x06", data=b"0,1.230E-05\r\n")

        state = VacuumState(single_gauge=True, p_load=9.99e-6)
        chamber, load = self.device.query_pressures(state)

        self.assertAlmostEqual(chamber, 1.23e-5, places=12)
        self.assertEqual(state.p_chamber_status, 0)
        # Im Single-Gauge-Modus bleibt der Loaddruck auf dem bisherigen Fallbackwert.
        self.assertAlmostEqual(load, 9.99e-6, places=12)

    def test_prx_parses_status_and_value_pairs_for_both_channels(self) -> None:
        self.transport.add_response(b"PRX\r", ack=b"\x06", data=b"0,1.00E-05,2,9.99E+02\r\n")

        state = VacuumState(single_gauge=False)
        chamber, load = self.device.query_pressures(state)

        self.assertAlmostEqual(chamber, 1.00e-5, places=12)
        self.assertAlmostEqual(load, 9.99e2, places=12)
        self.assertEqual(state.p_chamber_status, 0)
        self.assertEqual(state.p_load_status, 2)


class MaxiGaugeDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SerialSettings(port="COM_TEST", baudrate=9600, timeout=0.5)
        self.transport = _FakePfeifferTransport()
        self.device = MaxiGaugeDevice(  # type: ignore[arg-type]
            self.transport,
            self.settings,
            chamber_channel=3,
            load_channel=5,
        )

    def test_reads_configured_chamber_and_load_channels(self) -> None:
        self.transport.add_response(b"PR3\r", ack=b"\x06", data=b"0,4.00E-06\r\n")
        self.transport.add_response(b"PR5\r", ack=b"\x06", data=b"1,9.00E-07\r\n")

        state = VacuumState(single_gauge=False)
        chamber, load = self.device.query_pressures(state)

        self.assertAlmostEqual(chamber, 4.00e-6, places=12)
        self.assertAlmostEqual(load, 9.00e-7, places=12)
        self.assertEqual(state.p_chamber_status, 0)
        self.assertEqual(state.p_load_status, 1)

    def test_sensor_switch_command_uses_six_field_sen_mask(self) -> None:
        self.transport.add_response(b"SEN,0,0,2,0,0,0\r", ack=b"\x06", data=None)

        self.device.set_chamber_sensor(True)
        self.assertIn(b"SEN,0,0,2,0,0,0\r", self.transport.sent_payloads)


if __name__ == "__main__":
    unittest.main()

