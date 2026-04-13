from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal

from .config import (
    BAUD,
    EXPERT_ADDR,
    LOCK_FILE,
    MOTOR1_LEFT_TASTER_ACTIVE_LEVEL,
    MOTOR1_LEFT_TASTER_BIT,
    MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL,
    MOTOR1_RIGHT_TASTER_BIT,
    MOTOR1_SOFT_MAX_MM,
    MOTOR1_SOFT_MIN_MM,
    MOTOR2_LEFT_TASTER_ACTIVE_LEVEL,
    MOTOR2_LEFT_TASTER_BIT,
    MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL,
    MOTOR2_RIGHT_TASTER_BIT,
    MOTOR2_SOFT_MAX_MM,
    MOTOR2_SOFT_MIN_MM,
    PRESSURE,
    SERIAL,
    SIMULATION_SEED,
)
from .devices import (
    ExpertDevice,
    FUGDevice,
    MaxiGaugeDevice,
    NanotecDevice,
    NanotecValidationError,
    NoopTransport,
    PfeifferGaugeDeviceProtocol,
    PinnacleDevice,
    PlantInterlocks,
    PlantSimulator,
    PressureThresholds,
    SerialDeviceTransport,
    SerialSettings,
    TPG262GaugeDevice,
)
from .logging_utils import append_protocol_row, ensure_protocol_dir
from .models import MotorDirection, MotorState, PinnacleChannelState, PlantState, RegulationMode
from .runtime_settings import RuntimeSettings, default_runtime_settings


NanotecAction = Literal["start", "reference"]


@dataclass(frozen=True)
class NanotecPreflightReport:
    action: NanotecAction
    motor_index: int
    ok: bool
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    active_overrides: tuple[str, ...] = ()
    unlock_required: bool = False
    unlock_active: bool = False
    unlock_remaining_sec: float = 0.0


@dataclass
class Controller:
    state: PlantState = field(default_factory=PlantState)
    on_message: Callable[[str], None] = print
    runtime: RuntimeSettings | None = None

    def __post_init__(self) -> None:
        self._last_tick_s = time.monotonic()
        self._last_errors: dict[str, str] = {}
        self._last_pin_control_signature: tuple | None = None
        self._pinnacle_verify_after_apply: bool = True
        self._pinnacle_fast_emergency_off: bool = True
        self.runtime = self.runtime or default_runtime_settings()
        # Merkt, ob fuer einen Motor im aktuellen Lauf bereits ein automatisches
        # STOP wegen Endschalter ausgelöst wurde.
        #
        # Hintergrund:
        # Ohne diesen Latch wuerde der Controller in jedem Tick erneut STOP senden,
        # solange der Schalter aktiv bleibt. Das fuehrt zu unnötigem seriellen
        # Verkehr und unruhigen Logmeldungen.
        self._nanotec_limit_stop_latch: dict[int, bool] = {1: False, 2: False}
        self._nanotec_preflight_unlock_until: dict[tuple[NanotecAction, int], float] = {}
        self._nanotec_test_overrides: dict[str, bool] = {
            "service_mode": False,
            "allow_unknown_limit_inputs": False,
            "bypass_preflight_requirement": False,
            "bypass_active_limit_block_m1": False,
            "bypass_active_limit_block_m2": False,
            "bypass_soft_limit_block_m1": False,
            "bypass_soft_limit_block_m2": False,
        }

        self.state.simulation = bool(self.runtime.simulation)

        thresholds = PressureThresholds(
            valve_open_max=PRESSURE["valve_open_max"],
            valve_open_min=PRESSURE["valve_open_min"],
            bypass_min=PRESSURE["bypass_min"],
            argon_max_for_open=PRESSURE["argon_max_for_open"],
            pressure_max_age_sec=PRESSURE["max_age_sec"],
        )
        self.interlocks = PlantInterlocks(thresholds)
        self.simulator = PlantSimulator(seed=SIMULATION_SEED)

        self._transport = NoopTransport() if self.state.simulation else SerialDeviceTransport()

        self.fug_device = FUGDevice(self._transport, self._settings_for("fug"))
        self.dualg_device: PfeifferGaugeDeviceProtocol = self._build_pfeiffer_device()
        self.pinnacle_device = PinnacleDevice(self._transport, self._settings_for("pinnacle"))
        self.pinnacle_device.set_runtime_options(
            strict_protocol=True,
            query_retries=1,
            command_delay_s=0.05,
            response_read_size=64,
        )
        self.nanotec_device = NanotecDevice(self._transport, self._settings_for("nanotec"))
        self.expert_device = ExpertDevice(
            self._transport,
            self._settings_for("expert"),
            e9043_addr=EXPERT_ADDR["e9043"],
            e9053_addr=EXPERT_ADDR["e9053"],
            e9024_addr=EXPERT_ADDR["e9024"],
        )

        ensure_protocol_dir()
        LOCK_FILE.write_text("running", encoding="utf-8")

        if self.state.simulation:
            self._set_all_ports_connected(False)
            self.on_message("Simulation mode active")
        else:
            self._warn_if_shared_ports_in_real_mode()
            self._initialize_real_connections()

    def shutdown(self) -> None:
        LOCK_FILE.write_text("not running", encoding="utf-8")

    def tick(self) -> None:
        now = time.monotonic()
        dt = max(0.0, now - self._last_tick_s)
        self._last_tick_s = now

        if self.state.simulation:
            self.simulator.step(self.state, dt)
            self.state.vacuum.last_update_monotonic_s = now
        else:
            self._tick_real(now)

        append_protocol_row(self.state, datetime.now())

    def _tick_real(self, now: float) -> None:
        self._call_device("expert", self._tick_expert)
        self._call_device("dualg", lambda: self._tick_dualg(now))
        self._call_device("fug", self._tick_fug)
        self._tick_pinnacle()
        self._tick_nanotec_port(now)

    def _tick_expert(self) -> None:
        self.expert_device.tick(self.state.expert)
        self.expert_device.write_argon_setpoint(self.state.expert)

    def _tick_dualg(self, now_s: float) -> None:
        self.dualg_device.query_pressures(self.state.vacuum)
        self.state.vacuum.last_update_monotonic_s = now_s

    def _tick_fug(self) -> None:
        self.fug_device.query_actuals(self.state.fug)

    def _tick_pinnacle(self) -> None:
        channels = self._pinnacle_channels()
        # Explizit deaktivierter Port -> kein I/O, aber klarer Diagnosezustand.
        if not self._device_enabled("pinnacle"):
            self._mark_device_disabled("pinnacle")
            for channel in channels:
                channel.comm_ok = False
                channel.last_error = "pinnacle disabled (no port configured)"
            return

        # Signatur ueber alle Sollwerte:
        # Nur bei Aenderung wird ein Schreibzyklus gestartet.
        signature = self._pinnacle_control_signature(*channels)
        errors: list[str] = []
        successes = 0

        if signature != self._last_pin_control_signature:
            # Schreibpfad:
            # - A und B werden nacheinander angewendet
            # - bei Fehler wird nur der betroffene Kanal markiert
            all_writes_ok = True
            for idx, channel in enumerate(channels):
                channel_name = "A" if idx == 0 else "B"
                try:
                    self.pinnacle_device.apply_channel_control(
                        channel,
                        verify_after_apply=self._pinnacle_verify_after_apply,
                    )
                    channel.comm_ok = True
                    channel.last_error = ""
                    successes += 1
                except Exception as exc:
                    all_writes_ok = False
                    channel.comm_ok = False
                    channel.last_error = f"write failed: {exc}"
                    errors.append(f"CH{channel_name}@{int(channel.address)} write: {exc}")
            if all_writes_ok:
                # Nur bei komplett erfolgreichem A+B-Write wird die Signatur
                # als "abgearbeitet" gespeichert.
                self._last_pin_control_signature = signature

        # Readback-Pfad:
        # Die Istwerte werden in jedem Tick gelesen, damit GUI und Logik
        # immer den aktuellsten Kanalzustand sehen.
        for idx, channel in enumerate(channels):
            channel_name = "A" if idx == 0 else "B"
            try:
                self.pinnacle_device.read_channel(channel)
                channel.comm_ok = True
                channel.last_error = ""
                successes += 1
            except Exception as exc:
                channel.comm_ok = False
                channel.last_error = f"read failed: {exc}"
                errors.append(f"CH{channel_name}@{int(channel.address)} read: {exc}")

        if successes > 0:
            # Mindestens eine erfolgreiche Pinnacle-Kommunikation:
            # Port ist "connected", aber ggf. "failed" bei Teilfehlern.
            self._set_port_status("pinnacle", connected=True, failed=bool(errors), last_error="; ".join(errors))
            if errors:
                msg = "; ".join(errors)
                if self._last_errors.get("pinnacle") != msg:
                    self.on_message(f"pinnacle backend degraded: {msg}")
                    self._last_errors["pinnacle"] = msg
            else:
                self._last_errors.pop("pinnacle", None)
        else:
            # Kein einziger erfolgreicher Zugriff in diesem Tick.
            msg = "; ".join(errors) if errors else "no pinnacle response"
            self._set_port_status("pinnacle", connected=False, failed=True, last_error=msg)
            if self._last_errors.get("pinnacle") != msg:
                self.on_message(f"pinnacle backend error: {msg}")
                self._last_errors["pinnacle"] = msg

    def _tick_nanotec_port(self, now: float) -> None:
        """
        Fuehrt den zyklischen Nanotec-Poll robust und fehlertolerant aus.

        Warum nicht einfach `_call_device("nanotec", ...)`?
        - Der alte Pfad behandelte beide Motoren als einen einzigen Block.
        - Wenn z. B. Motor 1 ausfaellt, Motor 2 aber sauber antwortet, wollen wir
          Motor 2 weiterhin weiterpollen koennen.
        - Zusaetzlich soll der Portstatus korrekt `connected=False` zeigen, wenn
          aktuell kein Motor verbunden ist.
        """

        polled_any_motor = False
        successful_motor_polls = 0
        errors: list[str] = []

        for motor_index, motor in ((1, self.state.motor1), (2, self.state.motor2)):
            if not motor.connected:
                continue
            polled_any_motor = True

            try:
                self.nanotec_device.poll_motor(motor, now_s=now)
                self._update_motor_limit_states_from_expert(motor_index, motor)
                self._enforce_motor_limit_stop_if_required(motor_index, motor)
                successful_motor_polls += 1
                self._last_errors.pop(f"nanotec_m{motor_index}", None)
            except Exception as exc:
                msg = f"motor {motor_index} poll failed: {exc}"
                errors.append(msg)
                motor.status_text = f"Poll error: {exc}"
                if self._last_errors.get(f"nanotec_m{motor_index}") != msg:
                    self.on_message(f"nanotec backend warning: {msg}")
                    self._last_errors[f"nanotec_m{motor_index}"] = msg

        if not polled_any_motor:
            # Wichtig fuer die Bedienoberflaeche:
            # Wenn kein Motor verbunden ist, darf der Nanotec-Port nicht als
            # "connected=True" erscheinen.
            self._set_port_status(
                "nanotec",
                connected=False,
                failed=False,
                ready=True,
                last_error="No connected Nanotec motor",
            )
            self._last_errors.pop("nanotec", None)
            return

        if not errors:
            self._set_port_status("nanotec", connected=True, failed=False, last_error="")
            self._last_errors.pop("nanotec", None)
            return

        combined = "; ".join(errors)
        self._set_port_status(
            "nanotec",
            connected=(successful_motor_polls > 0),
            failed=True,
            last_error=combined,
        )
        if self._last_errors.get("nanotec") != combined:
            self.on_message(f"nanotec backend degraded: {combined}")
            self._last_errors["nanotec"] = combined

    def _initialize_real_connections(self) -> None:
        # Initialisierung nur fuer explizit konfigurierte Ports.
        # Hintergrund:
        # - Fuer "Pinnacle-only"-Betrieb sollen nicht konfigurierte Geraete die
        #   Laufzeit nicht mit vermeidbaren Fehlern fluten.
        # - Ein leerer Portname bedeutet daher bewusst "dieses Backend ist aus".
        if self._device_enabled("expert"):
            self._set_port_status("expert", connected=self.expert_device.check_connection(), failed=False)
        else:
            self._mark_device_disabled("expert")

        if self._device_enabled("dualg"):
            self._set_port_status("dualg", connected=self.dualg_device.check_connection(), failed=False)
        else:
            self._mark_device_disabled("dualg")

        if self._device_enabled("fug"):
            self._set_port_status("fug", connected=self.fug_device.check_connection(), failed=False)
        else:
            self._mark_device_disabled("fug")

        if self._device_enabled("pinnacle"):
            pin_addresses = (
                int(self.state.pin_a.address) & 0xFF,
                int(self.state.pin_b.address) & 0xFF,
            )
            pin_unique = tuple(dict.fromkeys(pin_addresses))
            pin_ok_any = self.pinnacle_device.check_connection(pin_unique, require_all=False)
            pin_ok_all = self.pinnacle_device.check_connection(pin_unique, require_all=True)
            self._set_port_status("pinnacle", connected=pin_ok_any, failed=(not pin_ok_all))
        else:
            self._mark_device_disabled("pinnacle")
            # Klarer Kanalzustand fuer GUI/Diagnose.
            self.state.pin_a.comm_ok = False
            self.state.pin_b.comm_ok = False
            self.state.pin_a.last_error = "pinnacle disabled (no port configured)"
            self.state.pin_b.last_error = "pinnacle disabled (no port configured)"

        if self._device_enabled("nanotec"):
            motor1_ok = self.nanotec_device.check_motor(self.state.motor1.address)
            motor2_ok = self.nanotec_device.check_motor(self.state.motor2.address)
            self.state.motor1.connected = motor1_ok
            self.state.motor2.connected = motor2_ok
            self._set_port_status("nanotec", connected=(motor1_ok or motor2_ok), failed=False)

            if motor1_ok:
                self.nanotec_device.configure_motor(self.state.motor1)
            if motor2_ok:
                self.nanotec_device.configure_motor(self.state.motor2)
        else:
            self.state.motor1.connected = False
            self.state.motor2.connected = False
            self._mark_device_disabled("nanotec")

        if self.state.ports["fug"].connected:
            self.fug_device.apply_initial_settings(self.state.fug)

    def _warn_if_shared_ports_in_real_mode(self) -> None:
        """
        Gibt eine deutliche Warnung aus, wenn mehrere Backends denselben Port teilen.

        Warum diese Warnung wichtig ist:
        - Unter Windows kann genau ein Prozess/ein Handle den COM-Port exklusiv
          oeffnen.
        - Wenn mehrere Geraeteklassen im selben Programm denselben Portnamen nutzen,
          entstehen typischerweise "Access is denied" / Timeout-Fehler.
        - Im Legacy-Projekt waren mehrere `COM3`-Defaults hinterlegt. Fuer echten
          Betrieb brauchen wir eine bewusst gepruefte, eindeutige Portzuordnung.
        """

        reverse: dict[str, list[str]] = {}
        for device_key, port_name in self.runtime.ports.items():
            normalized = str(port_name).strip()
            reverse.setdefault(normalized, []).append(device_key)

        duplicates = {port: keys for port, keys in reverse.items() if port and len(keys) > 1}
        if not duplicates:
            return

        parts = [f"{port} -> {', '.join(sorted(keys))}" for port, keys in sorted(duplicates.items())]
        self.on_message(
            "WARNING: shared serial port assignment detected for real mode: "
            + "; ".join(parts)
            + ". Please configure unique ports in the runtime settings (GUI or settings JSON)."
        )

    @staticmethod
    def _expert_e9053_bit(state: PlantState, bit_index: int) -> int | None:
        """
        Liest ein Expert-E9053 Ruecklesebit ueber eine lineare Bitnummer.

        Mapping:
        - 0..7   -> `e9053_do1[0..7]`
        - 8..15  -> `e9053_do2[0..7]`
        - sonst  -> `None` (kein valides Mapping)
        """

        if bit_index < 0:
            return None
        if 0 <= bit_index <= 7:
            return int(state.expert.e9053_do1[bit_index])
        if 8 <= bit_index <= 15:
            return int(state.expert.e9053_do2[bit_index - 8])
        return None

    @staticmethod
    def _bit_is_active(raw_value: int | None, active_level: int) -> bool | None:
        """
        Interpretiert den Rohbitwert gemaess konfiguriertem Aktivpegel.

        Rueckgabe:
        - True/False bei gueltigem Rohwert
        - None falls kein Rohwert verfuegbar war (z. B. Bit nicht gemappt)
        """

        if raw_value is None:
            return None
        return int(raw_value) == int(active_level)

    def _motor_limit_mapping(self, motor_index: int) -> tuple[int, int, int, int]:
        """
        Liefert die konfigurierte Endschalterzuordnung fuer einen Motor.

        Rueckgabe:
        - `(left_bit, right_bit, left_active_level, right_active_level)`
        """

        if motor_index == 1:
            return (
                MOTOR1_LEFT_TASTER_BIT,
                MOTOR1_RIGHT_TASTER_BIT,
                MOTOR1_LEFT_TASTER_ACTIVE_LEVEL,
                MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL,
            )
        if motor_index == 2:
            return (
                MOTOR2_LEFT_TASTER_BIT,
                MOTOR2_RIGHT_TASTER_BIT,
                MOTOR2_LEFT_TASTER_ACTIVE_LEVEL,
                MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL,
            )
        raise ValueError("motor_index must be 1 or 2")

    @staticmethod
    def _motor_soft_limits(motor_index: int) -> tuple[float | None, float | None]:
        """
        Liefert optionale Software-Fahrgrenzen fuer den gewaehlten Motor.

        Rueckgabe:
        - `(soft_min_mm, soft_max_mm)`

        Bedeutung:
        - `None` auf einer Seite bedeutet "kein Softwarelimit auf dieser Seite".
        """

        if motor_index == 1:
            return MOTOR1_SOFT_MIN_MM, MOTOR1_SOFT_MAX_MM
        if motor_index == 2:
            return MOTOR2_SOFT_MIN_MM, MOTOR2_SOFT_MAX_MM
        raise ValueError("motor_index must be 1 or 2")

    def _motor_limit_states(self, motor_index: int) -> tuple[bool | None, bool | None]:
        """
        Liest den aktuellen linken/rechten Endschalterzustand fuer einen Motor.
        """

        left_bit, right_bit, left_active_level, right_active_level = self._motor_limit_mapping(motor_index)
        left_raw = self._expert_e9053_bit(self.state, left_bit)
        right_raw = self._expert_e9053_bit(self.state, right_bit)
        return (
            self._bit_is_active(left_raw, left_active_level),
            self._bit_is_active(right_raw, right_active_level),
        )

    def _update_motor_limit_states_from_expert(self, motor_index: int, motor: MotorState) -> None:
        """
        Spiegelt Endschalterinformationen in den MotorState.

        Dadurch kann die GUI diese Safety-Information direkt aus dem Motorobjekt
        lesen, und der State bleibt fuer Logs/Diagnosen vollstaendig.
        """

        left_active, right_active = self._motor_limit_states(motor_index)
        motor.limit_left_active = bool(left_active) if left_active is not None else False
        motor.limit_right_active = bool(right_active) if right_active is not None else False

    def _would_move_into_active_limit(
        self,
        motor_index: int,
        direction: MotorDirection,
    ) -> tuple[bool, str]:
        """
        Prueft, ob die angeforderte Fahrtrichtung direkt in einen aktiven Endschalter fuehrt.

        Rueckgabe:
        - `(True, reason)` wenn geblockt werden soll
        - `(False, "")` wenn kein Blockgrund vorliegt
        """

        left_active, right_active = self._motor_limit_states(motor_index)
        left_bit, right_bit, _left_lvl, _right_lvl = self._motor_limit_mapping(motor_index)

        if direction == MotorDirection.LEFT and left_active is True:
            return True, f"left limit is active (bit {left_bit})"
        if direction == MotorDirection.RIGHT and right_active is True:
            return True, f"right limit is active (bit {right_bit})"
        return False, ""

    def _would_move_outside_soft_limits(
        self,
        motor_index: int,
        motor: MotorState,
        direction: MotorDirection,
    ) -> tuple[bool, str]:
        """
        Prueft, ob die angeforderte Bewegung ausserhalb konfigurierter
        Software-Fahrgrenzen enden wuerde.

        Modellannahme (wie im Legacy-Verhalten der Python-Portierung):
        - Richtung `LEFT`  erhoeht die Positionsachse.
        - Richtung `RIGHT` verringert die Positionsachse.

        Falls im realen Aufbau die Achsenrichtung invertiert ist, muessen die
        Soft-Limits entsprechend eingestellt werden.
        """

        soft_min_mm, soft_max_mm = self._motor_soft_limits(motor_index)
        if soft_min_mm is None and soft_max_mm is None:
            return False, ""

        travel_mm = abs(float(motor.target_position_mm))
        current_mm = float(motor.actual_position_mm)
        if direction == MotorDirection.LEFT:
            predicted_mm = current_mm + travel_mm
        else:
            predicted_mm = current_mm - travel_mm

        if soft_min_mm is not None and predicted_mm < soft_min_mm:
            return (
                True,
                "predicted target would cross software MIN limit "
                f"({predicted_mm:.3f} mm < {soft_min_mm:.3f} mm)",
            )
        if soft_max_mm is not None and predicted_mm > soft_max_mm:
            return (
                True,
                "predicted target would cross software MAX limit "
                f"({predicted_mm:.3f} mm > {soft_max_mm:.3f} mm)",
            )
        return False, ""

    def _would_reference_push_outside_soft_limits(
        self,
        motor_index: int,
        motor: MotorState,
        direction: MotorDirection,
    ) -> tuple[bool, str]:
        """
        Sicherheitspruefung fuer Referenzfahrten bei aktivierten Soft-Limits.

        Hintergrund:
        - Eine Referenzfahrt laeuft bis zum Trigger/Limit und hat keine feste
          Distanz im Sollwert.
        - Deshalb pruefen wir konservativ:
          Wenn der Motor bereits am/ausserhalb konfigurierter Grenze steht und
          die Referenzrichtung weiter nach "aussen" fuehrt, wird geblockt.
        """

        soft_min_mm, soft_max_mm = self._motor_soft_limits(motor_index)
        current_mm = float(motor.actual_position_mm)

        if direction == MotorDirection.LEFT and soft_max_mm is not None and current_mm >= soft_max_mm:
            return (
                True,
                "reference direction points outside software MAX limit "
                f"(current {current_mm:.3f} mm >= {soft_max_mm:.3f} mm)",
            )
        if direction == MotorDirection.RIGHT and soft_min_mm is not None and current_mm <= soft_min_mm:
            return (
                True,
                "reference direction points outside software MIN limit "
                f"(current {current_mm:.3f} mm <= {soft_min_mm:.3f} mm)",
            )
        return False, ""

    @staticmethod
    def _normalize_nanotec_action(action: str) -> NanotecAction:
        token = str(action).strip().lower()
        if token not in {"start", "reference"}:
            raise ValueError("nanotec action must be 'start' or 'reference'")
        return token  # type: ignore[return-value]

    @staticmethod
    def _motor_override_key(base_name: str, motor_index: int) -> str:
        if motor_index not in {1, 2}:
            raise ValueError("motor_index must be 1 or 2")
        return f"{base_name}_m{motor_index}"

    def _override_is_active(self, key: str) -> bool:
        if key == "service_mode":
            return bool(self._nanotec_test_overrides.get("service_mode", False))
        if not self._nanotec_test_overrides.get("service_mode", False):
            return False
        return bool(self._nanotec_test_overrides.get(key, False))

    def get_nanotec_test_overrides(self) -> dict[str, bool]:
        return dict(self._nanotec_test_overrides)

    def set_nanotec_test_override(self, name: str, enabled: bool, *, motor_index: int | None = None) -> None:
        token = str(name).strip().lower()
        if token in {"bypass_active_limit_block", "bypass_soft_limit_block"}:
            if motor_index not in {1, 2}:
                raise ValueError("motor_index must be 1 or 2 for motor-specific overrides")
            key = self._motor_override_key(token, int(motor_index))
        else:
            key = token
            if motor_index is not None:
                raise ValueError("motor_index is only supported for motor-specific overrides")

        if key not in self._nanotec_test_overrides:
            raise ValueError(f"unknown nanotec test override '{name}'")

        self._nanotec_test_overrides[key] = bool(enabled)
        if key == "service_mode" and not enabled:
            self.reset_nanotec_test_overrides()
            self.on_message("Nanotec service mode disabled. All test overrides reset.")
            return

        if key in {"service_mode", "bypass_preflight_requirement"}:
            self._invalidate_nanotec_preflight_unlocks()

        state_text = "enabled" if bool(enabled) else "disabled"
        self.on_message(f"Nanotec test override {key} {state_text}")

    def reset_nanotec_test_overrides(self) -> None:
        for key in list(self._nanotec_test_overrides):
            self._nanotec_test_overrides[key] = False
        self._invalidate_nanotec_preflight_unlocks()

    def _collect_active_overrides(self) -> tuple[str, ...]:
        active: list[str] = []
        for key in sorted(self._nanotec_test_overrides):
            if key == "service_mode":
                continue
            if self._override_is_active(key):
                active.append(key)
        return tuple(active)

    def _nanotec_unlock_required(self) -> bool:
        if self.state.simulation:
            return False
        if self._override_is_active("bypass_preflight_requirement"):
            return False
        return True

    def _nanotec_unlock_remaining_sec(self, action: NanotecAction, motor_index: int) -> float:
        if not self._nanotec_unlock_required():
            return 0.0
        now = time.monotonic()
        expiry = float(self._nanotec_preflight_unlock_until.get((action, motor_index), 0.0))
        return max(0.0, expiry - now)

    def _is_nanotec_preflight_unlocked(self, action: NanotecAction, motor_index: int) -> bool:
        if not self._nanotec_unlock_required():
            return True
        return self._nanotec_unlock_remaining_sec(action, motor_index) > 0.0

    def _invalidate_nanotec_preflight_unlocks(
        self,
        *,
        action: NanotecAction | None = None,
        motor_index: int | None = None,
    ) -> None:
        keys = list(self._nanotec_preflight_unlock_until.keys())
        for key_action, key_motor in keys:
            if action is not None and key_action != action:
                continue
            if motor_index is not None and key_motor != motor_index:
                continue
            self._nanotec_preflight_unlock_until.pop((key_action, key_motor), None)

    def _direction_limit_state(self, motor_index: int, direction: MotorDirection) -> tuple[bool | None, int, str]:
        left_active, right_active = self._motor_limit_states(motor_index)
        left_bit, right_bit, _left_lvl, _right_lvl = self._motor_limit_mapping(motor_index)
        if direction == MotorDirection.LEFT:
            return left_active, left_bit, "left"
        return right_active, right_bit, "right"

    def _active_limit_guard_result(
        self,
        motor_index: int,
        direction: MotorDirection,
        *,
        warnings: list[str] | None = None,
    ) -> tuple[bool, str]:
        state, bit, side = self._direction_limit_state(motor_index, direction)

        if state is None:
            if self._override_is_active("allow_unknown_limit_inputs"):
                if warnings is not None:
                    warnings.append(
                        f"{side} limit input unavailable (bit {bit}); allowed by override allow_unknown_limit_inputs"
                    )
                return False, ""
            return True, f"{side} limit input unavailable (bit {bit})"

        if state is True:
            key = self._motor_override_key("bypass_active_limit_block", motor_index)
            if self._override_is_active(key):
                if warnings is not None:
                    warnings.append(f"{side} limit is active (bit {bit}); active-limit block bypassed by {key}")
                return False, ""
            return True, f"{side} limit is active (bit {bit})"

        return False, ""

    def _soft_limit_guard_result(
        self,
        action: NanotecAction,
        motor_index: int,
        motor: MotorState,
        direction: MotorDirection,
        *,
        warnings: list[str] | None = None,
    ) -> tuple[bool, str]:
        if action == "start":
            should_block, reason = self._would_move_outside_soft_limits(motor_index, motor, direction)
        else:
            should_block, reason = self._would_reference_push_outside_soft_limits(motor_index, motor, direction)
        if not should_block:
            return False, ""

        key = self._motor_override_key("bypass_soft_limit_block", motor_index)
        if self._override_is_active(key):
            if warnings is not None:
                warnings.append(f"{reason}; soft-limit block bypassed by {key}")
            return False, ""
        return True, reason

    def nanotec_preflight(self, action: NanotecAction, motor_index: int) -> NanotecPreflightReport:
        action_token = self._normalize_nanotec_action(action)
        motor = self._motor_by_index(motor_index)
        blocking: list[str] = []
        warnings: list[str] = []

        if self.state.simulation:
            return NanotecPreflightReport(
                action=action_token,
                motor_index=motor_index,
                ok=True,
                warnings=("simulation mode active; hardware preflight checks skipped",),
                active_overrides=self._collect_active_overrides(),
                unlock_required=False,
                unlock_active=True,
                unlock_remaining_sec=0.0,
            )

        if not motor.connected:
            blocking.append("motor is not connected")

        if action_token == "start" and motor.running:
            blocking.append("motor is already running")

        direction = motor.reference_direction if action_token == "reference" else motor.direction
        block, reason = self._active_limit_guard_result(motor_index, direction, warnings=warnings)
        if block:
            blocking.append(reason)

        block, reason = self._soft_limit_guard_result(action_token, motor_index, motor, direction, warnings=warnings)
        if block:
            blocking.append(reason)

        unlock_required = self._nanotec_unlock_required()
        unlock_remaining = self._nanotec_unlock_remaining_sec(action_token, motor_index)
        unlock_active = (not unlock_required) or unlock_remaining > 0.0

        return NanotecPreflightReport(
            action=action_token,
            motor_index=motor_index,
            ok=(len(blocking) == 0),
            blocking_reasons=tuple(blocking),
            warnings=tuple(warnings),
            active_overrides=self._collect_active_overrides(),
            unlock_required=unlock_required,
            unlock_active=unlock_active,
            unlock_remaining_sec=unlock_remaining,
        )

    def arm_nanotec_preflight(self, action: NanotecAction, motor_index: int, ttl_sec: int = 20) -> float:
        action_token = self._normalize_nanotec_action(action)
        if self.state.simulation:
            return time.monotonic()

        report = self.nanotec_preflight(action_token, motor_index)
        if not report.ok:
            raise RuntimeError("Preflight failed: " + "; ".join(report.blocking_reasons))

        ttl = max(1, int(ttl_sec))
        expires_at = time.monotonic() + float(ttl)
        self._nanotec_preflight_unlock_until[(action_token, motor_index)] = expires_at
        self.on_message(f"Nanotec preflight armed for {action_token} M{motor_index} ({ttl}s window)")
        return expires_at

    def _log_preflight_block(self, action: NanotecAction, report: NanotecPreflightReport) -> None:
        reason_text = "; ".join(report.blocking_reasons) if report.blocking_reasons else "unknown reason"
        self.on_message(f"Motor {report.motor_index} {action} blocked for safety: {reason_text}")

    def _check_nanotec_action_preconditions(self, action: NanotecAction, motor_index: int) -> bool:
        report = self.nanotec_preflight(action, motor_index)
        if not report.ok:
            self._log_preflight_block(action, report)
            return False

        if self._is_nanotec_preflight_unlocked(action, motor_index):
            return True

        self.on_message(
            f"Motor {motor_index} {action} blocked: preflight not armed or expired "
            "(run preflight in UI; validity 20s)."
        )
        return False

    def _enforce_motor_limit_stop_if_required(self, motor_index: int, motor: MotorState) -> None:
        """
        Erzwingt einen sicheren STOP, wenn ein laufender Motor in einen Endschalter laeuft.

        Designziele:
        - Kein Dauerspammen von STOP-Befehlen (Latch pro Motor).
        - Klare, einmalige Logmeldung mit Seitenangabe (left/right).
        - Safety zuerst: im Zweifelsfall STOP versuchen.
        """

        if not motor.running:
            self._nanotec_limit_stop_latch[motor_index] = False
            motor.limit_stop_reason = ""
            return

        direction = motor.reference_direction if int(motor.status_code) == 160 else motor.direction
        should_stop, reason = self._active_limit_guard_result(motor_index, direction)
        if not should_stop:
            self._nanotec_limit_stop_latch[motor_index] = False
            return

        if self._nanotec_limit_stop_latch.get(motor_index, False):
            return

        self._nanotec_limit_stop_latch[motor_index] = True
        try:
            self.nanotec_device.stop_profile(motor)
            motor.limit_stop_reason = reason
            motor.status_text = f"Stopped by limit switch: {reason}"
            self.on_message(f"Motor {motor_index} safety stop: {reason}")
        except Exception as exc:
            self.on_message(f"Motor {motor_index} safety stop failed ({reason}): {exc}")

    def _build_pfeiffer_device(self) -> PfeifferGaugeDeviceProtocol:
        """
        Erstellt den passenden Pfeiffer-Treiber abhängig von der Laufzeitkonfiguration.

        Warum diese Factory-Methode?
        - Der Controller bleibt modular: einheitliche API, austauschbarer Treiber.
        - Wir können damit einfach zwischen TPG262 und MaxiGauge wechseln,
          ohne den restlichen Anlagenablauf umzubauen.
        """

        settings = self._settings_for("dualg")
        if self.runtime.pfeiffer_controller == "maxigauge":
            self.state.vacuum.single_gauge = False
            self.on_message(
                "Pfeiffer backend active: MaxiGauge "
                f"(chamber CH{self.runtime.pfeiffer_maxi_chamber_channel}, "
                f"load CH{self.runtime.pfeiffer_maxi_load_channel})"
            )
            return MaxiGaugeDevice(
                self._transport,
                settings,
                chamber_channel=self.runtime.pfeiffer_maxi_chamber_channel,
                load_channel=self.runtime.pfeiffer_maxi_load_channel,
            )

        self.state.vacuum.single_gauge = bool(self.runtime.pfeiffer_single_gauge)
        mode_text = "single-gauge (PR1 only)" if self.state.vacuum.single_gauge else "dual-gauge (PRX/PR1+PR2)"
        self.on_message(f"Pfeiffer backend active: TPG262 ({mode_text})")
        return TPG262GaugeDevice(self._transport, settings)

    def _settings_for(self, key: str) -> SerialSettings:
        opts = SERIAL[key]
        return SerialSettings(
            port=self.runtime.ports[key],
            baudrate=BAUD[key],
            parity=str(opts["parity"]),
            bytesize=int(opts["bytesize"]),
            stopbits=int(opts["stopbits"]),
            timeout=float(opts["timeout"]),
        )

    def _device_port(self, key: str) -> str:
        """
        Liefert den aktuell konfigurierten Portnamen fuer ein Backend.

        Ein leerer Rueckgabewert bedeutet bewusst: Backend ist deaktiviert.
        """

        return str(self.runtime.ports.get(key, "")).strip()

    def _device_enabled(self, key: str) -> bool:
        """
        True, wenn fuer das Backend ein nicht-leerer Port gesetzt ist.
        """

        return bool(self._device_port(key))

    def _mark_device_disabled(self, key: str) -> None:
        """
        Markiert ein Backend als bewusst deaktiviert (kein Fehlerzustand).
        """

        self._set_port_status(
            key,
            connected=False,
            failed=False,
            ready=False,
            last_error="disabled (no port configured)",
        )
        self._last_errors.pop(key, None)

    def _call_device(self, key: str, fn: Callable[[], None]) -> bool:
        # Einheitliche Deaktivierungslogik:
        # Wenn kein Port eingetragen ist, wird das Backend nicht ausgefuehrt.
        if not self._device_enabled(key):
            self._mark_device_disabled(key)
            return False

        try:
            fn()
            self._set_port_status(key, connected=True, failed=False, last_error="")
            self._last_errors.pop(key, None)
            return True
        except Exception as exc:
            msg = str(exc)
            self._set_port_status(key, connected=False, failed=True, last_error=msg)
            if key == "dualg":
                # Wichtige Safety-/Diagnose-Regel:
                # Bei Gauge-Kommunikationsfehlern markieren wir die Messwerte
                # explizit als ungueltig, statt alte Werte weiter "gut" wirken
                # zu lassen.
                self.state.vacuum.p_chamber = float("nan")
                self.state.vacuum.p_load = float("nan")
                self.state.vacuum.p_chamber_status = 6
                self.state.vacuum.p_load_status = 6
                self.state.vacuum.last_update_monotonic_s = 0.0
            if self._last_errors.get(key) != msg:
                self.on_message(f"{key} backend error: {msg}")
                self._last_errors[key] = msg
            return False

    def _set_all_ports_connected(self, connected: bool) -> None:
        for key in self.state.ports:
            self._set_port_status(key, connected=connected, failed=False)

    def _set_port_status(
        self,
        key: str,
        *,
        connected: bool,
        failed: bool,
        ready: bool = True,
        last_error: str | None = None,
    ) -> None:
        status = self.state.ports[key]
        status.connected = connected
        status.failed = failed
        status.ready = ready
        if last_error is not None:
            status.last_error = last_error

    def _pinnacle_channels(self) -> tuple[PinnacleChannelState, ...]:
        """
        Liefert alle aktuell im PlantState gefuehrten Pinnacle-Kanaele.

        Warum als eigene Methode?
        - Heute gibt es A/B.
        - Morgen koennen weitere Kanaele hinzukommen, ohne dass mehrere
          Controller-Stellen angepasst werden muessen.
        """

        return (self.state.pin_a, self.state.pin_b)

    @staticmethod
    def _pinnacle_control_signature(*channels: PinnacleChannelState) -> tuple:
        """
        Baut eine kompakte Signatur ueber alle Pinnacle-Sollwerte.

        Zweck:
        - Der Controller sendet nur dann neue Sollwerte, wenn sich diese
          Signatur geaendert hat.
        - So vermeiden wir unnoetigen seriellen Schreibverkehr pro Tick.
        """

        parts: list[object] = []
        for channel in channels:
            # Reihenfolge ist absichtlich stabil:
            # Aenderungen an genau diesen Feldern triggern einen neuen Write.
            parts.extend(
                [
                    # Adresse (1 Byte im Protokoll)
                    int(channel.address) & 0xFF,
                    # Gewuenschter Output-Status
                    bool(channel.active),
                    # Modus als String ("Power"/"Voltage"/"Current")
                    channel.mode.value,
                    # Rohindizes fuer Pulsparameter
                    int(channel.pulse_frequency_index),
                    int(channel.pulse_reverse_index),
                    # Setpoint float auf 6 Nachkommastellen normalisiert,
                    # damit numerisches Rauschen keinen Fake-Write ausloest.
                    round(float(channel.setpoint), 6),
                ]
            )
        return tuple(parts)

    def get_pinnacle_runtime_options(self) -> dict[str, float | int | bool]:
        return {
            "strict_protocol": bool(self.pinnacle_device.strict_protocol),
            "verify_after_apply": bool(self._pinnacle_verify_after_apply),
            "query_retries": int(self.pinnacle_device.query_retries),
            "command_delay_s": float(self.pinnacle_device.command_delay_s),
            "response_read_size": int(self.pinnacle_device.response_read_size),
            "fast_emergency_off": bool(self._pinnacle_fast_emergency_off),
        }

    def get_pinnacle_serial_settings(self) -> dict[str, str | int | float]:
        s = self.pinnacle_device.settings
        return {
            "port": str(s.port),
            "baudrate": int(s.baudrate),
            "parity": str(s.parity),
            "bytesize": int(s.bytesize),
            "stopbits": int(s.stopbits),
            "timeout": float(s.timeout),
        }

    def set_pinnacle_serial_settings(
        self,
        *,
        port: str | None = None,
        baudrate: int | None = None,
        parity: str | None = None,
        bytesize: int | None = None,
        stopbits: int | None = None,
        timeout: float | None = None,
    ) -> None:
        # Wir lesen zuerst das aktuelle Objekt, damit Teilupdates moeglich sind
        # (nur ein Feld aendern, Rest unveraendert lassen).
        current = self.pinnacle_device.settings
        new_settings = SerialSettings(
            port=str(port).strip() if port is not None else current.port,
            baudrate=int(baudrate) if baudrate is not None else int(current.baudrate),
            parity=str(parity).strip().upper() if parity is not None else str(current.parity).upper(),
            bytesize=int(bytesize) if bytesize is not None else int(current.bytesize),
            stopbits=int(stopbits) if stopbits is not None else int(current.stopbits),
            timeout=float(timeout) if timeout is not None else float(current.timeout),
        )
        self.pinnacle_device.settings = new_settings
        # Neue Port-/Serialparameter bedeuten: alte Signatur ist technisch
        # nicht mehr "abgearbeitet" und darf neu geschrieben werden.
        self._last_pin_control_signature = None
        self.on_message(
            "Pinnacle serial settings updated: "
            f"port={new_settings.port}, baud={new_settings.baudrate}, parity={new_settings.parity}, "
            f"bytesize={new_settings.bytesize}, stopbits={new_settings.stopbits}, timeout={new_settings.timeout:.3f}s"
        )

    def set_pinnacle_runtime_options(
        self,
        *,
        strict_protocol: bool | None = None,
        verify_after_apply: bool | None = None,
        query_retries: int | None = None,
        command_delay_s: float | None = None,
        response_read_size: int | None = None,
        fast_emergency_off: bool | None = None,
    ) -> None:
        self.pinnacle_device.set_runtime_options(
            strict_protocol=strict_protocol,
            query_retries=query_retries,
            command_delay_s=command_delay_s,
            response_read_size=response_read_size,
        )
        if verify_after_apply is not None:
            # Controller-seitige Verifikation nach Write.
            self._pinnacle_verify_after_apply = bool(verify_after_apply)
        if fast_emergency_off is not None:
            # Erlaubt direkten DC_OFF-Write im Not-Aus-Pfad.
            self._pinnacle_fast_emergency_off = bool(fast_emergency_off)

        self.on_message(
            "Pinnacle runtime options updated: "
            f"strict={self.pinnacle_device.strict_protocol}, "
            f"verify={self._pinnacle_verify_after_apply}, "
            f"retries={self.pinnacle_device.query_retries}, "
            f"delay={self.pinnacle_device.command_delay_s:.3f}s, "
            f"read_size={self.pinnacle_device.response_read_size}, "
            f"fast_off={self._pinnacle_fast_emergency_off}"
        )

    def set_pinnacle_channel_address(self, channel: str, address: int) -> None:
        ch = self._pinnacle_channel_by_name(channel)
        addr = int(address)
        if not (1 <= addr <= 255):
            raise ValueError("pinnacle address must be in range 1..255")
        ch.address = addr
        # Neue Adresse => alte Signatur ist ungültig und muss neu auf das
        # Geraet geschrieben werden.
        self._last_pin_control_signature = None
        self.on_message(f"Pinnacle channel {str(channel).upper()} address set to {addr}")

    def ping_pinnacle_channels(self) -> dict[str, bool]:
        mapping = {
            "A": self.state.pin_a.address,
            "B": self.state.pin_b.address,
        }
        result: dict[str, bool] = {}
        if self.state.simulation:
            return {"A": True, "B": True}
        if not self._device_enabled("pinnacle"):
            return {"A": False, "B": False}
        for key, address in mapping.items():
            result[key] = self.pinnacle_device.ping_address(int(address))
        return result

    # --- High-level API used by GUI and scripts ---

    def toggle_argon(self) -> None:
        changed = self._run_interlock_action("argon toggle", lambda: self.interlocks.toggle_argon(self.state))
        if changed:
            self.state.expert.argon_set = max(0.0, self.state.expert.argon_set)

    def set_argon_setpoint(self, value: float) -> None:
        value = max(0.0, min(40.0, float(value)))
        self.state.expert.argon_set = value

    def set_vat_chamber(self, mode: int) -> None:
        self._run_interlock_action(
            f"set VAT chamber mode={int(mode)}",
            lambda: self.interlocks.set_vat_chamber(self.state, mode),
        )

    def toggle_bypass_load(self) -> None:
        self._run_interlock_action("bypass load toggle", lambda: self.interlocks.toggle_bypass_load(self.state))

    def toggle_vat_load(self) -> None:
        self._run_interlock_action("VAT load toggle", lambda: self.interlocks.toggle_vat_load(self.state))

    def toggle_back_valve_load(self) -> None:
        self._run_interlock_action("back valve load toggle", lambda: self.interlocks.toggle_back_valve_load(self.state))

    def toggle_bypass_chamber(self) -> None:
        self._run_interlock_action("bypass chamber toggle", lambda: self.interlocks.toggle_bypass_chamber(self.state))

    def toggle_back_valve_chamber(self) -> None:
        self._run_interlock_action(
            "back valve chamber toggle",
            lambda: self.interlocks.toggle_back_valve_chamber(self.state),
        )

    def toggle_gate_load(self) -> None:
        self._run_interlock_action("gate load toggle", lambda: self.interlocks.toggle_gate_load(self.state))

    def set_chamber_sensor(self, on: bool) -> None:
        """
        Schaltet den Chamber-Sensor logisch und (im Realbetrieb) physisch.

        Sicherheits-/Konsistenzprinzip:
        - Wir setzen zuerst den gewuenschten Sollwert im State.
        - Danach versuchen wir den realen Hardware-Befehl.
        - Falls der Hardware-Schritt fehlschlaegt, rollen wir den Sollwert
          auf den vorherigen Stand zurueck.

        Damit vermeiden wir den Zustand "State sagt Sensor EIN, Hardware hat aber
        nicht umgeschaltet".
        """

        target = bool(on)
        previous = self.state.vacuum.chamber_sensor_on
        self.state.vacuum.chamber_sensor_on = target
        if self.state.simulation:
            return

        ok = self._call_device("dualg", lambda: self.dualg_device.set_chamber_sensor(target))
        if not ok:
            self.state.vacuum.chamber_sensor_on = previous

    def set_load_sensor(self, on: bool) -> None:
        """
        Schaltet den Load-Sensor logisch und (im Realbetrieb) physisch.

        Gleiche Rollback-Strategie wie bei `set_chamber_sensor`, um Soll-/Ist-
        Inkonsistenzen bei Kommunikationsfehlern zu vermeiden.
        """

        target = bool(on)
        previous = self.state.vacuum.load_sensor_on
        self.state.vacuum.load_sensor_on = target
        if self.state.simulation:
            return

        ok = self._call_device("dualg", lambda: self.dualg_device.set_load_sensor(target))
        if not ok:
            self.state.vacuum.load_sensor_on = previous

    # --- Pfeiffer advanced command API (for Pumpen-Detailfenster) ---

    def pfeiffer_channel_count(self) -> int:
        runtime = self.get_runtime_settings()
        return 6 if runtime.pfeiffer_controller == "maxigauge" else 2

    def _validate_pfeiffer_channel(self, channel: int) -> int:
        ch = int(channel)
        max_channel = self.pfeiffer_channel_count()
        if not (1 <= ch <= max_channel):
            raise ValueError(f"Pfeiffer channel must be in range 1..{max_channel}, got {channel!r}")
        return ch

    def _run_pfeiffer_action(self, action: Callable[[], None], *, command_name: str) -> None:
        if self.state.simulation:
            raise RuntimeError(f"Pfeiffer command '{command_name}' is only available in real mode.")
        ok = self._call_device("dualg", action)
        if not ok:
            detail = self.state.ports["dualg"].last_error or "unknown Pfeiffer error"
            raise RuntimeError(f"Pfeiffer command '{command_name}' failed: {detail}")

    def _run_pfeiffer_action_with_result(self, action: Callable[[], Any], *, command_name: str) -> Any:
        if self.state.simulation:
            raise RuntimeError(f"Pfeiffer command '{command_name}' is only available in real mode.")

        box: dict[str, Any] = {}

        def _wrapped() -> None:
            box["value"] = action()

        ok = self._call_device("dualg", _wrapped)
        if not ok:
            detail = self.state.ports["dualg"].last_error or "unknown Pfeiffer error"
            raise RuntimeError(f"Pfeiffer command '{command_name}' failed: {detail}")
        return box.get("value")

    def _sync_sensor_shadow_for_channel(self, channel: int, on: bool) -> None:
        runtime = self.get_runtime_settings()
        if runtime.pfeiffer_controller == "maxigauge":
            if channel == int(runtime.pfeiffer_maxi_chamber_channel):
                self.state.vacuum.chamber_sensor_on = bool(on)
            if channel == int(runtime.pfeiffer_maxi_load_channel):
                self.state.vacuum.load_sensor_on = bool(on)
            return

        if channel == 1:
            self.state.vacuum.chamber_sensor_on = bool(on)
        if channel == 2:
            self.state.vacuum.load_sensor_on = bool(on)

    def pfeiffer_query_ascii(self, command: str) -> str:
        cmd = str(command).strip()
        if not cmd:
            raise ValueError("Pfeiffer query command must not be empty.")
        value = self._run_pfeiffer_action_with_result(
            lambda: self.dualg_device.query_ascii(cmd),
            command_name=cmd,
        )
        return str(value)

    def pfeiffer_write_ascii(self, command: str) -> None:
        cmd = str(command).strip()
        if not cmd:
            raise ValueError("Pfeiffer write command must not be empty.")
        self._run_pfeiffer_action(
            lambda: self.dualg_device.write_ascii(cmd),
            command_name=cmd,
        )

    def pfeiffer_read_channel(self, channel: int) -> tuple[int, float]:
        ch = self._validate_pfeiffer_channel(channel)

        def _read() -> tuple[int, float]:
            reading = self.dualg_device.read_channel(ch)
            if reading is None:
                raise RuntimeError(f"PR{ch} returned no parsable payload.")
            return int(reading.status), float(reading.value)

        status, value = self._run_pfeiffer_action_with_result(_read, command_name=f"PR{ch}")
        return int(status), float(value)

    def pfeiffer_get_unit(self) -> int:
        return int(self._run_pfeiffer_action_with_result(self.dualg_device.get_unit, command_name="UNI"))

    def pfeiffer_set_unit(self, unit_code: int) -> None:
        code = int(unit_code)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_unit(code),
            command_name=f"UNI,{code}",
        )

    def pfeiffer_get_sensor_onoff(self) -> list[int]:
        value = self._run_pfeiffer_action_with_result(self.dualg_device.get_sensor_onoff, command_name="SEN")
        return [int(v) for v in value]

    def pfeiffer_set_sensor_channel(self, channel: int, on: bool) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_sensor_onoff(ch, bool(on)),
            command_name="SEN",
        )
        self._sync_sensor_shadow_for_channel(ch, bool(on))

    def pfeiffer_set_degas(self, channel: int, on: bool) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_degas(ch, bool(on)),
            command_name="DGS",
        )

    def pfeiffer_set_filter(self, channel: int, value: int) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        val = int(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_filter(ch, val),
            command_name="FIL",
        )

    def pfeiffer_set_calibration(self, channel: int, value: float) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        val = float(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_calibration(ch, val),
            command_name="CAL/CAx",
        )

    def pfeiffer_set_fsr(self, channel: int, value: int) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        val = int(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_fsr(ch, val),
            command_name="FSR",
        )

    def pfeiffer_set_ofc(self, channel: int, value: int) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        val = int(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_ofc(ch, val),
            command_name="OFC",
        )

    def pfeiffer_set_channel_name(self, channel: int, name: str) -> None:
        ch = self._validate_pfeiffer_channel(channel)
        token = str(name).strip()
        if not token:
            raise ValueError("Channel name must not be empty.")
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_channel_name(ch, token),
            command_name="CID",
        )

    def pfeiffer_set_digits(self, value: int) -> None:
        val = int(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_digits(val),
            command_name="DCD",
        )

    def pfeiffer_set_contrast(self, value: int) -> None:
        val = int(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_contrast(val),
            command_name="DCC",
        )

    def pfeiffer_set_screensave(self, value: int) -> None:
        val = int(value)
        self._run_pfeiffer_action(
            lambda: self.dualg_device.set_screensave(val),
            command_name="DCS",
        )

    def pfeiffer_factory_reset(self) -> None:
        self._run_pfeiffer_action(
            self.dualg_device.factory_reset,
            command_name="SAV",
        )

    def pfeiffer_device_info_lines(self) -> list[str]:
        value = self._run_pfeiffer_action_with_result(
            self.dualg_device.device_info_lines,
            command_name="diagnose",
        )
        return [str(line) for line in value]

    def set_fug_hv(self, on: bool) -> None:
        self.state.fug.hv_on = bool(on)
        if not self.state.simulation:
            self._call_device("fug", lambda: self.fug_device.set_hv(self.state.fug, on))

    def set_fug_voltage_setpoint(self, value: float) -> None:
        if self.state.simulation:
            self.state.fug.voltage_set = abs(float(value))
            return
        self._call_device("fug", lambda: self.fug_device.set_voltage_setpoint(self.state.fug, value))

    def set_fug_current_setpoint(self, value: float) -> None:
        if self.state.simulation:
            self.state.fug.current_set = abs(float(value))
            return
        self._call_device("fug", lambda: self.fug_device.set_current_setpoint(self.state.fug, value))

    def set_fug_voltage_ramp(self, value: float) -> None:
        if self.state.simulation:
            self.state.fug.voltage_ramp = abs(float(value))
            return
        self._call_device("fug", lambda: self.fug_device.set_voltage_ramp(self.state.fug, value))

    def set_fug_current_ramp(self, value: float) -> None:
        if self.state.simulation:
            self.state.fug.current_ramp = abs(float(value))
            return
        self._call_device("fug", lambda: self.fug_device.set_current_ramp(self.state.fug, value))

    def set_pinnacle_channel_active(self, channel: str, on: bool) -> None:
        """
        Setzt den gewuenschten ON/OFF-Zustand eines Pinnacle-Kanals.

        Hinweis:
        - Diese Methode aendert den Sollwert im State.
        - Das physische Schreiben auf das Geraet erfolgt im naechsten Tick
          ueber den zentralen Pinnacle-Schreibpfad.
        """

        ch = self._pinnacle_channel_by_name(channel)
        # Nur Sollstatus im State; physischer Write folgt im Tick.
        ch.active = bool(on)

    def set_pinnacle_channel_mode(self, channel: str, mode: RegulationMode | str) -> None:
        """
        Setzt den Regelmodus eines Pinnacle-Kanals.

        Erlaubte Modi:
        - Power
        - Voltage
        - Current

        Ungueltige Eingaben werden bewusst auf `Current` zurueckgefuehrt,
        weil das auch im Legacy-Code der sichere Standard war.
        """

        ch = self._pinnacle_channel_by_name(channel)
        if isinstance(mode, RegulationMode):
            parsed = mode
        else:
            # Stringeingaben robust vereinheitlichen.
            normalized = str(mode).strip().lower()
            parsed = {
                "power": RegulationMode.POWER,
                "voltage": RegulationMode.VOLTAGE,
                "current": RegulationMode.CURRENT,
            }.get(normalized, RegulationMode.CURRENT)
        ch.mode = parsed

    def set_pinnacle_channel_setpoint(self, channel: str, value: float) -> None:
        """
        Setzt den Sollwert (Setpoint) fuer einen Pinnacle-Kanal.

        Schutzmechanik:
        - Negative Werte werden auf 0.0 begrenzt.
        - Sehr grosse Werte werden auf 65535 begrenzt (16-Bit-Protokollgrenze).
        """

        ch = self._pinnacle_channel_by_name(channel)
        value_f = max(0.0, float(value))
        # Rohprotokoll ist 16 Bit -> harte Obergrenze.
        ch.setpoint = min(65535.0, value_f)

    def set_pinnacle_channel_pulse_frequency_khz(self, channel: str, value_khz: float) -> None:
        """
        Setzt die Pulsfrequenz in kHz.

        Legacy-Abbildung:
        - Das Protokoll arbeitet intern mit einem Index.
        - 1 Indexschritt entspricht 5 kHz.
        """

        ch = self._pinnacle_channel_by_name(channel)
        # kHz -> Protokollindex (1 Schritt = 5 kHz).
        idx = int(round(float(value_khz) / 5.0))
        ch.pulse_frequency_index = max(0, min(255, idx))

    def set_pinnacle_channel_pulse_reverse_us(self, channel: str, value_us: float) -> None:
        """
        Setzt die Puls-Umkehrzeit in Mikrosekunden.

        Legacy-Abbildung:
        - Das Protokoll arbeitet intern mit einem Index.
        - 1 Indexschritt entspricht 0.1 us.
        """

        ch = self._pinnacle_channel_by_name(channel)
        # us -> Protokollindex (1 Schritt = 0.1 us).
        idx = int(round(float(value_us) / 0.1))
        ch.pulse_reverse_index = max(0, min(255, idx))

    def emergency_pinnacle_off_all(self) -> None:
        """
        Fail-safe-Hilfsmethode fuer GUI/Bedienung.

        Diese Methode setzt beide Kanaele sofort logisch auf AUS.
        Der physische OFF-Befehl wird im naechsten Tick geschrieben.
        """

        self.state.pin_a.active = False
        self.state.pin_b.active = False
        # Signatur zuruecksetzen erzwingt nachfolgend sicheren OFF-Writepfad.
        self._last_pin_control_signature = None

        if self.state.simulation or (not self._pinnacle_fast_emergency_off):
            return

        errors: list[str] = []
        for key, channel in (("A", self.state.pin_a), ("B", self.state.pin_b)):
            # Optionaler "Fast Path": sofortiger direkter OFF-Befehl.
            ok = self.pinnacle_device.force_output_off(int(channel.address))
            if not ok:
                errors.append(f"{key}@{int(channel.address)}")
        if errors:
            self.on_message("Pinnacle fast emergency-off partial failure on: " + ", ".join(errors))

    def _pinnacle_channel_by_name(self, channel: str) -> PinnacleChannelState:
        """
        Kanal-Resolver fuer lesbare, robuste GUI-Anbindung.

        Erlaubte Namen:
        - \"A\" oder \"PIN_A\"
        - \"B\" oder \"PIN_B\"
        """

        token = str(channel).strip().upper()
        if token in {"A", "PIN_A"}:
            return self.state.pin_a
        if token in {"B", "PIN_B"}:
            return self.state.pin_b
        raise ValueError("pinnacle channel must be 'A' or 'B'")

    def configure_motor(
        self,
        motor_index: int,
        *,
        target_speed: float,
        target_position_mm: float,
        step_mode: int,
        direction: MotorDirection | str,
        reference_direction: MotorDirection | str,
        loops: int,
    ) -> bool:
        """
        Uebernimmt Motor-Sollwerte mit Validierung und (im Realbetrieb) sofortigem
        Hardware-Apply.

        Warum diese Methode wichtig ist:
        - Die GUI soll nicht direkt in Roh-Attribute schreiben.
        - Wir bekommen so einen zentralen, sicheren Pfad fuer alle Fahrparameter.
        - Bei Hardwarefehlern rollen wir auf den vorherigen Motorzustand zurueck,
          damit der sichtbare Sollzustand konsistent bleibt.
        """

        motor = self._motor_by_index(motor_index)
        if motor.running:
            self.on_message(f"Motor {motor_index} parameter update rejected: motor is currently running")
            return False

        snapshot = self._snapshot_motor_profile(motor)
        try:
            motor.target_speed = self.nanotec_device.normalize_target_speed(int(round(float(target_speed))))
            motor.target_position_mm = self.nanotec_device.normalize_target_position_mm(float(target_position_mm))
            motor.step_mode_to_set = self.nanotec_device.normalize_step_mode(int(step_mode))
            motor.loops = self.nanotec_device.normalize_loops(int(round(float(loops))))
            motor.direction = self._parse_motor_direction(direction)
            motor.reference_direction = self._parse_motor_direction(reference_direction)
        except (ValueError, NanotecValidationError) as exc:
            self._restore_motor_profile(motor, snapshot)
            raise NanotecValidationError(f"Motor {motor_index} parameter validation failed: {exc}") from exc

        if self.state.simulation:
            # In der Simulation synchronisieren wir den aktiven Schrittmodus sofort
            # auf den Sollmodus, damit die GUI den erwarteten Zustand sieht.
            motor.step_mode_active = motor.step_mode_to_set
            self._invalidate_nanotec_preflight_unlocks(motor_index=motor_index)
            self.on_message(f"Motor {motor_index} parameters updated (simulation)")
            return True

        if not motor.connected:
            self._restore_motor_profile(motor, snapshot)
            self.on_message(f"Motor {motor_index} parameter update rejected: motor is not connected")
            return False

        ok = self._call_device("nanotec", lambda: self.nanotec_device.configure_motor(motor))
        if ok:
            self._invalidate_nanotec_preflight_unlocks(motor_index=motor_index)
            self.on_message(f"Motor {motor_index} parameters applied to Nanotec controller")
            return True

        self._restore_motor_profile(motor, snapshot)
        self.on_message(
            f"Motor {motor_index} parameter update rolled back: Nanotec device apply failed, previous values restored"
        )
        return False

    def start_motor(self, motor_index: int) -> None:
        """
        Startet eine Profilfahrt fuer den angegebenen Motor.

        Sicherheitsregeln:
        - Kein zweiter Start, wenn bereits laufend.
        - Im Realbetrieb nur bei erkannter Motorverbindung.
        - Die finale Parameter-Validierung passiert im Nanotec-Treiber direkt vor
          dem Startkommando.
        """

        motor = self._motor_by_index(motor_index)
        if motor.running:
            self.on_message(f"Motor {motor_index} already running")
            return

        if self.state.simulation:
            motor.running = True
            motor.runtime_sec = 0.0
            motor.expected_runtime_sec = self.simulator._estimate_motor_runtime(motor)
            motor.rest_sec = motor.expected_runtime_sec
            motor.status_code = 16
            motor.status_text = "Motor running (simulation)"
            self._nanotec_limit_stop_latch[motor_index] = False
            self.on_message(f"Motor {motor_index} started (simulation)")
            return

        if not self._check_nanotec_action_preconditions("start", motor_index):
            return

        self._nanotec_limit_stop_latch[motor_index] = False
        self._call_device("nanotec", lambda: self.nanotec_device.start_profile(motor, now_s=time.monotonic()))

    def stop_motor(self, motor_index: int) -> None:
        """
        Stoppt eine laufende Profilfahrt.
        """

        motor = self._motor_by_index(motor_index)
        if self.state.simulation:
            motor.running = False
            motor.runtime_sec = 0.0
            motor.rest_sec = 0.0
            motor.status_code = 17
            motor.status_text = "Controller ready (simulation)"
            self._nanotec_limit_stop_latch[motor_index] = False
            self.on_message(f"Motor {motor_index} stopped (simulation)")
            return

        if not motor.connected:
            self.on_message(f"Motor {motor_index} stop blocked: motor is not connected")
            return

        self._nanotec_limit_stop_latch[motor_index] = False
        self._invalidate_nanotec_preflight_unlocks(action="start", motor_index=motor_index)
        self._call_device("nanotec", lambda: self.nanotec_device.stop_profile(motor))

    def stop_all_motors(self) -> None:
        """
        Komfort-/Sicherheitsfunktion: fordert STOP fuer beide Motoren an.
        """

        self.stop_motor(1)
        self.stop_motor(2)

    def reference_motor(self, motor_index: int) -> None:
        """
        Startet die Referenzfahrt fuer den angegebenen Motor.
        """

        motor = self._motor_by_index(motor_index)
        if self.state.simulation:
            motor.running = True
            motor.status_code = 160
            motor.status_text = "Reference run (simulation)"
            self._nanotec_limit_stop_latch[motor_index] = False
            self.on_message(f"Motor {motor_index} reference run (simulation)")
            return

        if not self._check_nanotec_action_preconditions("reference", motor_index):
            return

        self._nanotec_limit_stop_latch[motor_index] = False
        self._call_device("nanotec", lambda: self.nanotec_device.start_reference(motor))

    def reconnect_pfeiffer(self) -> bool:
        """
        Fuehrt einen gezielten Reconnect-Check fuer den Pfeiffer-Druckpfad aus.

        Warum diese Methode wichtig ist:
        - In der C++-Oberflaeche gab es explizite Port-Connect-Buttons.
        - Im Python-Rewrite lief die Druckabfrage bislang nur zyklisch "implizit".
        - Mit dieser Methode kann die GUI einen bewussten Reconnect anstossen,
          ohne den gesamten Controller neu zu starten.
        """

        if self.state.simulation:
            self.on_message("Pfeiffer reconnect skipped in simulation mode")
            return True

        def _reconnect() -> None:
            if not self.dualg_device.check_connection():
                raise RuntimeError("Pfeiffer gauge did not answer on configured port")

        ok = self._call_device("dualg", _reconnect)
        self._set_port_status("dualg", connected=ok, failed=not ok)
        if ok:
            self.on_message("Pfeiffer reconnect successful")
        return ok

    def reconnect_pinnacle(self) -> bool:
        """
        Fuehrt einen gezielten Reconnect-Check fuer den Pinnacle-MDX-Pfad aus.

        Ablauf:
        1) Verbindungscheck ueber ein echtes Query.
        2) Portstatus im gemeinsamen Runtime-State aktualisieren.
        3) Klaren Logeintrag schreiben.
        """

        if self.state.simulation:
            self.on_message("Pinnacle reconnect skipped in simulation mode")
            return True
        if not self._device_enabled("pinnacle"):
            self._mark_device_disabled("pinnacle")
            self.on_message("Pinnacle reconnect skipped: no Pinnacle port configured")
            return False

        # Beide Kanaele koennen auf derselben Adresse liegen; darum deduplizieren.
        addresses = (
            int(self.state.pin_a.address) & 0xFF,
            int(self.state.pin_b.address) & 0xFF,
        )
        unique_addresses = tuple(dict.fromkeys(addresses))
        # Pro Adresse exakt ein Ping.
        results = {addr: self.pinnacle_device.ping_address(addr) for addr in unique_addresses}
        any_ok = any(results.values())
        all_ok = all(results.values())

        detail = ", ".join(f"{addr}:{'ok' if ok else 'fail'}" for addr, ok in results.items())
        self._set_port_status("pinnacle", connected=any_ok, failed=(not all_ok), last_error=detail if not all_ok else "")

        if any_ok:
            self._last_pin_control_signature = None
            if all_ok:
                self.on_message("Pinnacle reconnect successful (all configured addresses responded)")
            else:
                self.on_message("Pinnacle reconnect partial success: " + detail)
            return True

        self.on_message("Pinnacle reconnect failed: " + detail)
        return False

    def reconnect_fug(self) -> bool:
        """
        Fuehrt einen gezielten Reconnect-Check fuer das FUG-Netzteil aus.

        Zusatzlogik:
        - Bei erfolgreicher Verbindung schreiben wir die aktuellen Sollwerte
          erneut als Initialdatensatz, damit der Geraetezustand und der Python-
          State wieder synchron sind.
        """

        if self.state.simulation:
            self.on_message("FUG reconnect skipped in simulation mode")
            return True

        def _reconnect() -> None:
            if not self.fug_device.check_connection():
                raise RuntimeError("FUG device did not answer on configured port")
            self.fug_device.apply_initial_settings(self.state.fug)

        ok = self._call_device("fug", _reconnect)
        self._set_port_status("fug", connected=ok, failed=not ok)
        if ok:
            self.on_message("FUG reconnect successful")
        return ok

    def reconnect_expert(self) -> bool:
        """
        Fuehrt einen gezielten Reconnect-Check fuer die Expert-I/O-Karte aus.

        Hinweis:
        - Wir pruefen nicht nur den Handshake, sondern versuchen danach direkt,
          eventuell ausstehende Ausgangsaenderungen zu uebertragen.
        - Damit ist der Reconnect fuer Bediener "vollstaendig": verbunden UND
          die aktuellen Sollausgaenge sind direkt wieder aktiv.
        """

        if self.state.simulation:
            self.on_message("Expert reconnect skipped in simulation mode")
            return True

        def _reconnect() -> None:
            if not self.expert_device.check_connection():
                raise RuntimeError("Expert I/O device did not answer on configured port")
            self.expert_device.apply_pending_outputs(self.state.expert)

        ok = self._call_device("expert", _reconnect)
        self._set_port_status("expert", connected=ok, failed=not ok)
        if ok:
            self.on_message("Expert reconnect successful")
        return ok

    def reconnect_nanotec(self) -> bool:
        """
        Prueft Nanotec-Verbindung und konfiguriert erkannte Motoren neu.

        Einsatzfall:
        - Nach Kabel-/Portwechsel aus der GUI heraus.
        """

        if self.state.simulation:
            self.on_message("Nanotec reconnect skipped in simulation mode")
            return True

        self._invalidate_nanotec_preflight_unlocks()

        def _reconnect() -> None:
            motor1_ok = self.nanotec_device.check_motor(self.state.motor1.address)
            motor2_ok = self.nanotec_device.check_motor(self.state.motor2.address)

            self.state.motor1.connected = motor1_ok
            self.state.motor2.connected = motor2_ok
            if not (motor1_ok or motor2_ok):
                raise RuntimeError("No Nanotec motor detected on configured addresses")

            if motor1_ok:
                self.nanotec_device.configure_motor(self.state.motor1)
            if motor2_ok:
                self.nanotec_device.configure_motor(self.state.motor2)

        ok = self._call_device("nanotec", _reconnect)
        self._set_port_status(
            "nanotec",
            connected=(self.state.motor1.connected or self.state.motor2.connected),
            failed=not ok,
        )
        if ok:
            self.on_message(
                f"Nanotec reconnect successful (M1={self.state.motor1.connected}, M2={self.state.motor2.connected})"
            )
        return ok

    def set_motor_addresses(self, address1: str, address2: str, *, reconnect: bool = True) -> bool:
        """
        Setzt die Nanotec-Motoradressen fuer Motor 1/2.

        Design:
        - Validierung zentral im Controller.
        - Bei Realbetrieb optional direkt Reconnect/Neuabgleich ausfuehren.
        """

        addr1 = str(address1).strip()
        addr2 = str(address2).strip()
        if not addr1.isdigit() or not addr2.isdigit():
            raise ValueError("Motoradressen muessen numerisch sein.")
        if not (1 <= int(addr1) <= 255):
            raise ValueError("Motoradresse 1 muss im Bereich 1..255 liegen.")
        if not (1 <= int(addr2) <= 255):
            raise ValueError("Motoradresse 2 muss im Bereich 1..255 liegen.")
        if addr1 == addr2:
            raise ValueError("Motoradresse 1 und 2 duerfen nicht identisch sein.")

        if self.state.motor1.running or self.state.motor2.running:
            raise RuntimeError("Motoradressen koennen nur im Stillstand geaendert werden.")

        old_addr1 = self.state.motor1.address
        old_addr2 = self.state.motor2.address
        old_m1_connected = bool(self.state.motor1.connected)
        old_m2_connected = bool(self.state.motor2.connected)
        old_port = copy.deepcopy(self.state.ports["nanotec"])

        self.state.motor1.address = addr1
        self.state.motor2.address = addr2
        self._invalidate_nanotec_preflight_unlocks()

        if self.state.simulation:
            self.state.motor1.connected = True
            self.state.motor2.connected = True
            self._set_port_status("nanotec", connected=True, failed=False, last_error="")
            self.on_message(f"Nanotec addresses updated (simulation): M1={addr1}, M2={addr2}")
            return True

        if not reconnect:
            self.on_message(f"Nanotec addresses updated (no reconnect): M1={addr1}, M2={addr2}")
            return True

        ok = self.reconnect_nanotec()
        if not ok:
            self.state.motor1.address = old_addr1
            self.state.motor2.address = old_addr2
            self.state.motor1.connected = old_m1_connected
            self.state.motor2.connected = old_m2_connected
            self._set_port_status(
                "nanotec",
                connected=old_port.connected,
                failed=old_port.failed,
                ready=old_port.ready,
                last_error=old_port.last_error,
            )
            self.on_message(
                "Nanotec reconnect after address update failed. "
                "Rolling back to previous addresses and retrying reconnect."
            )
            rollback_ok = self.reconnect_nanotec()
            if rollback_ok:
                self.on_message(f"Nanotec address rollback successful (M1={old_addr1}, M2={old_addr2}).")
            else:
                self.on_message(
                    f"Nanotec rollback reconnect failed. Manual check required (addresses currently M1={old_addr1}, M2={old_addr2})."
                )
            self.on_message(
                f"Nanotec reconnect after address update failed (M1={addr1}, M2={addr2})."
            )
        return ok

    def _motor_by_index(self, motor_index: int) -> MotorState:
        if motor_index == 1:
            return self.state.motor1
        if motor_index == 2:
            return self.state.motor2
        raise ValueError("motor_index must be 1 or 2")

    def get_runtime_settings(self) -> RuntimeSettings:
        return self.runtime

    def get_motor_soft_limits(self, motor_index: int) -> tuple[float | None, float | None]:
        """
        Liefert die aktuell konfigurierten Software-Fahrgrenzen fuer einen Motor.

        Diese Methode ist fuer GUIs gedacht, damit Safety-Grenzen sichtbar
        gemacht werden koennen.
        """

        return self._motor_soft_limits(motor_index)

    @staticmethod
    def _parse_motor_direction(value: MotorDirection | str) -> MotorDirection:
        """
        Konvertiert GUI-/Script-Eingaben robust in `MotorDirection`.
        """

        if isinstance(value, MotorDirection):
            return value
        token = str(value).strip().lower()
        if token in {"left", "links", "l"}:
            return MotorDirection.LEFT
        if token in {"right", "rechts", "r"}:
            return MotorDirection.RIGHT
        raise NanotecValidationError("direction must be 'left'/'right' (or links/rechts)")

    @staticmethod
    def _snapshot_motor_profile(motor: MotorState) -> dict[str, object]:
        return {
            "target_speed": motor.target_speed,
            "target_position_mm": motor.target_position_mm,
            "step_mode_to_set": motor.step_mode_to_set,
            "loops": motor.loops,
            "direction": motor.direction,
            "reference_direction": motor.reference_direction,
        }

    @staticmethod
    def _restore_motor_profile(motor: MotorState, snapshot: dict[str, object]) -> None:
        motor.target_speed = int(snapshot["target_speed"])
        motor.target_position_mm = float(snapshot["target_position_mm"])
        motor.step_mode_to_set = int(snapshot["step_mode_to_set"])
        motor.loops = int(snapshot["loops"])
        motor.direction = snapshot["direction"]  # type: ignore[assignment]
        motor.reference_direction = snapshot["reference_direction"]  # type: ignore[assignment]

    def _run_interlock_action(
        self,
        action_label: str,
        action: Callable[[], tuple[bool, str]],
    ) -> bool:
        """
        Führt eine Interlock-Aktion transaktional aus.

        Warum diese Methode wichtig ist:
        1) Die Interlock-Funktion ändert zuerst den Sollzustand im State.
        2) Danach wird die physische Ausgabe sofort auf die Expert-Karte geschrieben.
        3) Falls dieser Hardware-Schritt fehlschlägt, rollen wir den Sollzustand zurück.

        Dadurch reduzieren wir das Risiko "Software zeigt offen, Hardware blieb zu".
        """

        snapshot = self._snapshot_valve_and_outputs()
        changed, msg = action()
        self.on_message(msg)

        if not changed:
            return False
        if self.state.simulation:
            return True

        applied = self._call_device(
            "expert",
            lambda: self.expert_device.apply_pending_outputs(self.state.expert),
        )
        if applied:
            return True

        # Rollback auf alten Sollzustand und erneuter Schreibversuch, um den
        # logischen Zustand wieder mit der Hardware zusammenzuführen.
        self._restore_valve_and_outputs(snapshot)
        self.on_message(
            f"{action_label} rolled back: Expert output apply failed. "
            "Previous valve/output state restored in software."
        )
        self._call_device("expert", lambda: self.expert_device.apply_pending_outputs(self.state.expert))
        return False

    def _snapshot_valve_and_outputs(self) -> dict[str, object]:
        return {
            "valves": copy.deepcopy(self.state.valves),
            "di1": self.state.expert.e9043_di1[:],
            "di2": self.state.expert.e9043_di2[:],
            "di1_changed": self.state.expert.e9043_di1_changed[:],
            "di2_changed": self.state.expert.e9043_di2_changed[:],
        }

    def _restore_valve_and_outputs(self, snapshot: dict[str, object]) -> None:
        self.state.valves = copy.deepcopy(snapshot["valves"])
        self.state.expert.e9043_di1[:] = list(snapshot["di1"])  # type: ignore[arg-type]
        self.state.expert.e9043_di2[:] = list(snapshot["di2"])  # type: ignore[arg-type]
        self.state.expert.e9043_di1_changed[:] = [True] * len(self.state.expert.e9043_di1_changed)
        self.state.expert.e9043_di2_changed[:] = [True] * len(self.state.expert.e9043_di2_changed)
