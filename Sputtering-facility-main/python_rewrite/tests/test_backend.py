from __future__ import annotations

import unittest

from sputtering_app import protocols
from sputtering_app.controller import Controller
from sputtering_app.devices.interlocks import PlantInterlocks, PressureThresholds
from sputtering_app.devices.simulation import PlantSimulator
from sputtering_app.models import PlantState


class ProtocolTests(unittest.TestCase):
    def test_pinnacle_frame_crc_and_length(self) -> None:
        frame = protocols.pinnacle_frame(8, 6, b"\x34\x12")
        self.assertEqual(len(frame), 6)
        self.assertEqual(frame[0], 10)  # address + payload_len
        self.assertEqual(frame[1], 6)
        self.assertEqual(frame[2], 4)
        crc = 0
        for b in frame[:-1]:
            crc ^= b
        self.assertEqual(frame[-1], crc)


class InterlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = PlantState()
        self.interlocks = PlantInterlocks(
            PressureThresholds(
                valve_open_max=0.3,
                valve_open_min=2.0e-8,
                bypass_min=5.0e-3,
                argon_max_for_open=1.0e-5,
                pressure_max_age_sec=3.0,
            )
        )

    def test_argon_blocked_when_pressure_too_high(self) -> None:
        self.state.vacuum.p_chamber = 1.0e-3
        changed, msg = self.interlocks.toggle_argon(self.state)
        self.assertFalse(changed)
        self.assertIn("too high", msg)
        self.assertFalse(self.state.valves.ar_valve_open)

    def test_vat_chamber_open_requires_back_valve(self) -> None:
        self.state.vacuum.p_chamber = 1.0e-4
        self.state.vacuum.chamber_sensor_on = True
        self.state.vacuum.p_chamber_status = 0
        changed, _ = self.interlocks.set_vat_chamber(self.state, 2)
        self.assertFalse(changed)

        self.state.valves.back_valve_chamber_open = True
        changed, _ = self.interlocks.set_vat_chamber(self.state, 2)
        self.assertTrue(changed)
        self.assertEqual(self.state.valves.vat_chamber, 2)
        self.assertTrue(self.state.expert.e9043_di1_changed[3])

    def test_gate_toggle_sets_explicit_output_bit(self) -> None:
        changed, _ = self.interlocks.toggle_gate_load(self.state)
        self.assertTrue(changed)
        self.assertTrue(self.state.valves.gate_load_open)
        self.assertEqual(self.state.expert.e9043_di1[2], 1)
        self.assertTrue(self.state.expert.e9043_di1_changed[2])

        changed, _ = self.interlocks.toggle_gate_load(self.state)
        self.assertTrue(changed)
        self.assertFalse(self.state.valves.gate_load_open)
        self.assertEqual(self.state.expert.e9043_di1[2], 0)

    def test_real_mode_blocks_open_when_pressure_sample_missing(self) -> None:
        self.state.simulation = False
        self.state.ports["dualg"].connected = True
        self.state.ports["dualg"].failed = False
        self.state.ports["expert"].connected = True
        self.state.ports["expert"].failed = False
        self.state.vacuum.p_load = 1.0e-2
        self.state.vacuum.last_update_monotonic_s = 0.0

        changed, msg = self.interlocks.toggle_bypass_load(self.state)
        self.assertFalse(changed)
        self.assertIn("no pressure sample", msg)

    def test_real_mode_allows_back_valve_close_without_pressure_refresh(self) -> None:
        self.state.simulation = False
        self.state.ports["expert"].connected = True
        self.state.ports["expert"].failed = False
        self.state.valves.back_valve_load_open = True
        self.state.expert.e9043_di1[1] = 1

        changed, _ = self.interlocks.toggle_back_valve_load(self.state)
        self.assertTrue(changed)
        self.assertFalse(self.state.valves.back_valve_load_open)
        self.assertEqual(self.state.expert.e9043_di1[1], 0)


class SimulationTests(unittest.TestCase):
    def test_simulation_generates_values(self) -> None:
        state = PlantState()
        sim = PlantSimulator(seed=1)
        state.fug.hv_on = True
        state.pin_a.active = True
        state.pin_b.active = True
        sim.step(state, 0.5)
        self.assertGreater(state.vacuum.p_chamber, 0.0)
        self.assertGreaterEqual(state.fug.voltage_actual, 0.0)
        self.assertGreaterEqual(state.pin_a.power, 0.0)


class ControllerSensorRollbackTests(unittest.TestCase):
    """
    Regression-Tests fuer Soll/Ist-Konsistenz bei Sensor-Schaltkommandos.

    Ziel:
    Wenn die echte Hardware-Schaltoperation fehlschlaegt, darf der logische
    State nicht im gewuenschten Zielwert \"haengen bleiben\".
    """

    def setUp(self) -> None:
        # Der Controller startet in der Testumgebung standardmaessig im
        # Simulationsmodus. Fuer den Rollback-Test schalten wir danach
        # explizit auf \"real path\" um.
        self.ctrl = Controller(on_message=lambda _: None)
        self.ctrl.state.simulation = False

    def test_set_chamber_sensor_rolls_back_on_backend_failure(self) -> None:
        self.ctrl.state.vacuum.chamber_sensor_on = True

        # Simuliert einen fehlgeschlagenen Device-Aufruf.
        self.ctrl._call_device = lambda *_args, **_kwargs: False  # type: ignore[method-assign]

        self.ctrl.set_chamber_sensor(False)
        self.assertTrue(self.ctrl.state.vacuum.chamber_sensor_on)

    def test_set_load_sensor_rolls_back_on_backend_failure(self) -> None:
        self.ctrl.state.vacuum.load_sensor_on = True

        # Simuliert einen fehlgeschlagenen Device-Aufruf.
        self.ctrl._call_device = lambda *_args, **_kwargs: False  # type: ignore[method-assign]

        self.ctrl.set_load_sensor(False)
        self.assertTrue(self.ctrl.state.vacuum.load_sensor_on)


class ControllerReconnectTests(unittest.TestCase):
    """
    Tests fuer die neuen expliziten Reconnect-Hilfsmethoden im Controller.

    Ziel:
    - Die GUI soll diese Methoden gefahrlos aufrufen koennen.
    - Portstatus soll danach konsistent im gemeinsamen Runtime-State stehen.
    """

    def setUp(self) -> None:
        self.ctrl = Controller(on_message=lambda _: None)
        self.ctrl.state.simulation = False

    def test_reconnect_pfeiffer_success_sets_port_connected(self) -> None:
        self.ctrl.dualg_device.check_connection = lambda: True  # type: ignore[method-assign]
        ok = self.ctrl.reconnect_pfeiffer()
        self.assertTrue(ok)
        self.assertTrue(self.ctrl.state.ports["dualg"].connected)
        self.assertFalse(self.ctrl.state.ports["dualg"].failed)

    def test_reconnect_pfeiffer_failure_sets_port_failed(self) -> None:
        self.ctrl.dualg_device.check_connection = lambda: False  # type: ignore[method-assign]
        ok = self.ctrl.reconnect_pfeiffer()
        self.assertFalse(ok)
        self.assertFalse(self.ctrl.state.ports["dualg"].connected)
        self.assertTrue(self.ctrl.state.ports["dualg"].failed)

    def test_reconnect_pinnacle_success(self) -> None:
        # Reconnect prueft seit dem Mehrkanal-Update explizit beide
        # konfigurierten Pinnacle-Adressen via `ping_address`.
        self.ctrl.pinnacle_device.ping_address = lambda _addr: True  # type: ignore[method-assign]
        ok = self.ctrl.reconnect_pinnacle()
        self.assertTrue(ok)
        self.assertTrue(self.ctrl.state.ports["pinnacle"].connected)
        self.assertFalse(self.ctrl.state.ports["pinnacle"].failed)

    def test_reconnect_fug_success_applies_initial_settings(self) -> None:
        called = {"init": False}
        self.ctrl.fug_device.check_connection = lambda: True  # type: ignore[method-assign]

        def _fake_apply_initial_settings(_state):  # noqa: ANN001
            called["init"] = True

        self.ctrl.fug_device.apply_initial_settings = _fake_apply_initial_settings  # type: ignore[method-assign]
        ok = self.ctrl.reconnect_fug()
        self.assertTrue(ok)
        self.assertTrue(called["init"])
        self.assertTrue(self.ctrl.state.ports["fug"].connected)

    def test_reconnect_expert_success(self) -> None:
        called = {"apply": False}
        self.ctrl.expert_device.check_connection = lambda: True  # type: ignore[method-assign]

        def _fake_apply_pending(_state):  # noqa: ANN001
            called["apply"] = True

        self.ctrl.expert_device.apply_pending_outputs = _fake_apply_pending  # type: ignore[method-assign]
        ok = self.ctrl.reconnect_expert()
        self.assertTrue(ok)
        self.assertTrue(called["apply"])
        self.assertTrue(self.ctrl.state.ports["expert"].connected)


if __name__ == "__main__":
    unittest.main()
