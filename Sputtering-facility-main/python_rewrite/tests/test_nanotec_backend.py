from __future__ import annotations

import time
import unittest

from sputtering_app import protocols
import sputtering_app.controller as controller_module
from sputtering_app.controller import Controller
from sputtering_app.devices.nanotec import NanotecDevice, NanotecValidationError
from sputtering_app.devices.transport import SerialSettings
from sputtering_app.models import MotorDirection, MotorState


class _FakeNanotecTransport:
    """
    Sehr einfacher Fake-Transport fuer Nanotec-Tests.

    Wir trennen bewusst zwischen `write(...)` und `query(...)`, damit wir exakt
    pruefen koennen, welche Befehle geschrieben wurden und welche Antworten das
    Backend anschliessend verarbeitet.
    """

    def __init__(self) -> None:
        self.write_payloads: list[bytes] = []
        self.query_payloads: list[bytes] = []
        self._query_responses: dict[bytes, list[bytes]] = {}

    def add_query_response(self, payload: bytes, response: bytes) -> None:
        self._query_responses.setdefault(payload, []).append(response)

    def query(
        self,
        settings: SerialSettings,
        payload: bytes,
        *,
        read_size: int = 100,
        delay_after_write: float = 0.1,
    ) -> bytes:
        self.query_payloads.append(payload)
        queue = self._query_responses.get(payload, [])
        if queue:
            return queue.pop(0)
        return b""

    def write(
        self,
        settings: SerialSettings,
        payload: bytes,
        *,
        delay_after_write: float = 0.1,
    ) -> None:
        self.write_payloads.append(payload)


class NanotecDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = _FakeNanotecTransport()
        self.settings = SerialSettings(port="COM_TEST", baudrate=115200, timeout=0.8)
        self.device = NanotecDevice(self.transport, self.settings)  # type: ignore[arg-type]

    def test_configure_motor_writes_expected_legacy_sequence(self) -> None:
        motor = MotorState(address="1", target_position_mm=100.0)
        motor.target_speed = 1000
        motor.step_mode_to_set = 2
        motor.loops = 1

        self.transport.add_query_response(protocols.nanotec_cmd("1", "Zg"), b"g2\r")
        self.transport.add_query_response(protocols.nanotec_cmd("1", "C"), b"C1234\r")

        self.device.configure_motor(motor)

        self.assertEqual(
            self.transport.write_payloads,
            [
                protocols.nanotec_cmd("1", "S"),
                protocols.nanotec_cmd("1", "p1"),
                protocols.nanotec_cmd("1", "g2"),
                protocols.nanotec_cmd("1", "o1000"),
                protocols.nanotec_cmd("1", "s20000"),
                protocols.nanotec_cmd("1", "W1"),
                protocols.nanotec_cmd("1", "t1"),
            ],
        )
        self.assertEqual(motor.step_mode_active, 2)
        self.assertAlmostEqual(motor.actual_position_mm, 6.17, places=2)

    def test_start_profile_rejects_invalid_speed(self) -> None:
        motor = MotorState(address="1", target_position_mm=100.0)
        motor.target_speed = 0

        with self.assertRaises(NanotecValidationError):
            self.device.start_profile(motor)

    def test_target_position_rejects_negative_value(self) -> None:
        motor = MotorState(address="1", target_position_mm=-1.0)
        motor.target_speed = 100
        motor.step_mode_to_set = 2
        motor.loops = 1

        with self.assertRaises(NanotecValidationError):
            self.device.configure_motor(motor)

    def test_start_profile_applies_changed_step_mode_before_start(self) -> None:
        motor = MotorState(address="1", target_position_mm=10.0)
        motor.step_mode_active = 2
        motor.step_mode_to_set = 4
        motor.target_speed = 1000
        motor.loops = 1

        self.transport.add_query_response(protocols.nanotec_cmd("1", "Zg"), b"g4\r")
        self.transport.add_query_response(protocols.nanotec_cmd("1", "C"), b"C0\r")

        self.device.start_profile(motor, now_s=1.0)

        self.assertIn(protocols.nanotec_cmd("1", "g4"), self.transport.write_payloads)
        self.assertEqual(motor.step_mode_active, 4)
        self.assertTrue(motor.running)


class ControllerNanotecSafetyTests(unittest.TestCase):
    def _set_soft_limits(
        self,
        *,
        m1_min: float | None,
        m1_max: float | None,
        m2_min: float | None,
        m2_max: float | None,
    ) -> None:
        """
        Setzt Controller-Modulkonstanten fuer Soft-Limits testweise um.

        Wir stellen den Ursprungszustand per `addCleanup` wieder her, damit
        einzelne Tests sich nicht gegenseitig beeinflussen.
        """

        old_values = (
            controller_module.MOTOR1_SOFT_MIN_MM,
            controller_module.MOTOR1_SOFT_MAX_MM,
            controller_module.MOTOR2_SOFT_MIN_MM,
            controller_module.MOTOR2_SOFT_MAX_MM,
        )

        def _restore() -> None:
            (
                controller_module.MOTOR1_SOFT_MIN_MM,
                controller_module.MOTOR1_SOFT_MAX_MM,
                controller_module.MOTOR2_SOFT_MIN_MM,
                controller_module.MOTOR2_SOFT_MAX_MM,
            ) = old_values

        self.addCleanup(_restore)

        controller_module.MOTOR1_SOFT_MIN_MM = m1_min
        controller_module.MOTOR1_SOFT_MAX_MM = m1_max
        controller_module.MOTOR2_SOFT_MIN_MM = m2_min
        controller_module.MOTOR2_SOFT_MAX_MM = m2_max

    def test_configure_motor_rolls_back_on_device_apply_failure(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False

        motor = ctrl.state.motor1
        old_speed = motor.target_speed
        old_position = motor.target_position_mm
        old_step_mode = motor.step_mode_to_set
        old_direction = motor.direction
        old_reference_direction = motor.reference_direction
        old_loops = motor.loops

        # Simuliert Fehler beim Hardware-Apply.
        ctrl._call_device = lambda *_args, **_kwargs: False  # type: ignore[method-assign]

        ok = ctrl.configure_motor(
            1,
            target_speed=900,
            target_position_mm=250.0,
            step_mode=4,
            direction=MotorDirection.RIGHT,
            reference_direction=MotorDirection.LEFT,
            loops=2,
        )

        self.assertFalse(ok)
        self.assertEqual(motor.target_speed, old_speed)
        self.assertEqual(motor.target_position_mm, old_position)
        self.assertEqual(motor.step_mode_to_set, old_step_mode)
        self.assertEqual(motor.direction, old_direction)
        self.assertEqual(motor.reference_direction, old_reference_direction)
        self.assertEqual(motor.loops, old_loops)

    def test_configure_motor_in_simulation_updates_state(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = True

        ok = ctrl.configure_motor(
            1,
            target_speed=900,
            target_position_mm=250.0,
            step_mode=4,
            direction="right",
            reference_direction="left",
            loops=2,
        )

        self.assertTrue(ok)
        motor = ctrl.state.motor1
        self.assertEqual(motor.target_speed, 900)
        self.assertEqual(motor.target_position_mm, 250.0)
        self.assertEqual(motor.step_mode_to_set, 4)
        self.assertEqual(motor.step_mode_active, 4)
        self.assertEqual(motor.direction, MotorDirection.RIGHT)
        self.assertEqual(motor.reference_direction, MotorDirection.LEFT)
        self.assertEqual(motor.loops, 2)

    def test_start_is_blocked_when_target_direction_limit_is_active(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True
        ctrl.state.motor1.direction = MotorDirection.LEFT
        # Bit 11 => e9053_do2[3] (M1 left taster default mapping)
        ctrl.state.expert.e9053_do2[3] = 1

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.start_motor(1)
        self.assertFalse(called["value"])

    def test_start_is_blocked_when_soft_limit_would_be_crossed(self) -> None:
        self._set_soft_limits(m1_min=None, m1_max=620.0, m2_min=None, m2_max=None)
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True
        ctrl.state.motor1.direction = MotorDirection.LEFT
        ctrl.state.motor1.actual_position_mm = 600.0
        ctrl.state.motor1.target_position_mm = 50.0

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.start_motor(1)
        self.assertFalse(called["value"])

    def test_reference_is_blocked_when_reference_direction_limit_is_active(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True
        ctrl.state.motor1.reference_direction = MotorDirection.RIGHT
        # Bit 12 => e9053_do2[4] (M1 right taster default mapping)
        ctrl.state.expert.e9053_do2[4] = 1

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.reference_motor(1)
        self.assertFalse(called["value"])

    def test_reference_is_blocked_by_soft_limit_when_position_is_already_outside(self) -> None:
        self._set_soft_limits(m1_min=0.0, m1_max=None, m2_min=None, m2_max=None)
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True
        ctrl.state.motor1.reference_direction = MotorDirection.RIGHT
        ctrl.state.motor1.actual_position_mm = -0.5

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.reference_motor(1)
        self.assertFalse(called["value"])

    def test_start_requires_preflight_unlock_in_real_mode(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.start_motor(1)
        self.assertFalse(called["value"])

        ctrl.arm_nanotec_preflight("start", 1, ttl_sec=20)
        ctrl.start_motor(1)
        self.assertTrue(called["value"])

    def test_start_preflight_unlock_expires(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.arm_nanotec_preflight("start", 1, ttl_sec=20)
        ctrl._nanotec_preflight_unlock_until[("start", 1)] = time.monotonic() - 0.1
        ctrl.start_motor(1)
        self.assertFalse(called["value"])

    def test_preflight_unknown_limit_inputs_blocked_until_override(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor2.connected = True

        report = ctrl.nanotec_preflight("start", 2)
        self.assertFalse(report.ok)
        self.assertIn("limit input unavailable", "; ".join(report.blocking_reasons))

        ctrl.set_nanotec_test_override("service_mode", True)
        ctrl.set_nanotec_test_override("allow_unknown_limit_inputs", True)
        report2 = ctrl.nanotec_preflight("start", 2)
        self.assertTrue(report2.ok)

    def test_bypass_preflight_requirement_allows_direct_start(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True

        called = {"value": False}

        def _fake_call_device(*_args, **_kwargs):
            called["value"] = True
            return True

        ctrl._call_device = _fake_call_device  # type: ignore[method-assign]
        ctrl.set_nanotec_test_override("service_mode", True)
        ctrl.set_nanotec_test_override("bypass_preflight_requirement", True)
        ctrl.start_motor(1)
        self.assertTrue(called["value"])

    def test_set_motor_addresses_rolls_back_on_reconnect_failure(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False

        reconnect_calls: list[tuple[str, str]] = []

        def _fake_reconnect() -> bool:
            reconnect_calls.append((ctrl.state.motor1.address, ctrl.state.motor2.address))
            return len(reconnect_calls) > 1

        ctrl.reconnect_nanotec = _fake_reconnect  # type: ignore[method-assign]
        ok = ctrl.set_motor_addresses("3", "4", reconnect=True)

        self.assertFalse(ok)
        self.assertEqual(ctrl.state.motor1.address, "1")
        self.assertEqual(ctrl.state.motor2.address, "2")
        self.assertEqual(reconnect_calls, [("3", "4"), ("1", "2")])

    def test_nanotec_port_is_marked_disconnected_when_no_motor_is_connected(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = False
        ctrl.state.motor2.connected = False

        ctrl._tick_nanotec_port(time.monotonic())
        runtime = ctrl.state.ports["nanotec"]
        self.assertFalse(runtime.connected)
        self.assertFalse(runtime.failed)
        self.assertIn("No connected Nanotec motor", runtime.last_error)

    def test_nanotec_poll_errors_are_isolated_per_motor(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        ctrl.state.motor1.connected = True
        ctrl.state.motor2.connected = True

        def _fake_poll(motor, *, now_s=None):  # noqa: ANN001
            if motor.address == "1":
                raise RuntimeError("motor1 timeout")
            motor.status_code = 17
            motor.running = False

        ctrl.nanotec_device.poll_motor = _fake_poll  # type: ignore[method-assign]
        ctrl._tick_nanotec_port(time.monotonic())

        runtime = ctrl.state.ports["nanotec"]
        self.assertTrue(runtime.connected)
        self.assertTrue(runtime.failed)
        self.assertIn("motor 1 poll failed", runtime.last_error)

    def test_running_motor_gets_single_safety_stop_when_limit_is_hit(self) -> None:
        ctrl = Controller(on_message=lambda _: None)
        ctrl.state.simulation = False
        motor = ctrl.state.motor1
        motor.connected = True
        ctrl.state.motor2.connected = False
        motor.running = True
        motor.status_code = 16
        motor.direction = MotorDirection.LEFT

        # Linker Endschalter von Motor 1 aktiv (Bit 11 => e9053_do2[3]).
        ctrl.state.expert.e9053_do2[3] = 1

        def _fake_poll(_motor, *, now_s=None):  # noqa: ANN001
            # Simuliert, dass die Rueckmeldung noch "running" zeigt.
            _motor.running = True
            _motor.status_code = 16

        stop_counter = {"n": 0}

        def _fake_stop(_motor):  # noqa: ANN001
            stop_counter["n"] += 1

        ctrl.nanotec_device.poll_motor = _fake_poll  # type: ignore[method-assign]
        ctrl.nanotec_device.stop_profile = _fake_stop  # type: ignore[method-assign]

        ctrl._tick_nanotec_port(time.monotonic())
        ctrl._tick_nanotec_port(time.monotonic())

        self.assertEqual(stop_counter["n"], 1)
        self.assertIn("Stopped by limit switch", motor.status_text)


if __name__ == "__main__":
    unittest.main()
