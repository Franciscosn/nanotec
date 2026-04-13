from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..models import MODE_TO_REGULATION_CODE, MotorDirection, PlantState
from .expert import sync_simulated_expert_outputs


@dataclass
class PlantSimulator:
    seed: int = 12345
    _rng: random.Random = field(init=False)
    _motor_start_positions: dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def step(self, state: PlantState, dt_sec: float) -> None:
        sync_simulated_expert_outputs(state.expert, state.valves)
        self._simulate_vacuum(state)
        self._simulate_baratron(state)
        self._simulate_fug(state)
        self._simulate_pinnacle(state)
        self._simulate_motors(state, dt_sec)
        self._simulate_argon_actual(state)

    def _simulate_vacuum(self, state: PlantState) -> None:
        vac = state.vacuum
        io = state.expert

        v1 = vac.sim_v10 * self._noise(0.01)
        v2 = vac.sim_v20 * self._noise(0.01)

        if io.e9053_do1[2] == 1 and vac.sim_v20 > 0.05:
            vac.sim_v20 *= 0.9  # bypass load open
        if io.e9053_do1[3] == 1 and io.e9053_do1[6] == 1 and vac.sim_v20 > 2.0e-6:
            vac.sim_v20 *= 0.8  # back valve load + VAT load
        if io.e9053_do2[6] == 1 and vac.sim_v10 > 0.05:
            vac.sim_v10 *= 0.9  # bypass chamber open

        if state.valves.ar_valve_open:
            vac.sim_v10 += state.expert.argon_set / 20.0 * 4.0e-4

        # back valve chamber open + VAT chamber open
        if io.e9053_do2[7] == 1 and io.e9053_do2[0] == 0 and io.e9053_do2[1] == 1 and io.e9053_do2[2] == 0:
            if vac.sim_v10 > 1.0e-6:
                vac.sim_v10 *= 0.8

        # back valve chamber open + VAT chamber half-open
        if io.e9053_do2[7] == 1 and io.e9053_do2[0] == 1 and io.e9053_do2[1] == 1 and io.e9053_do2[2] == 1:
            if vac.sim_v10 > 1.0e-6:
                vac.sim_v10 *= 0.95

        if not vac.chamber_sensor_on:
            v1 = 0.02
        if not vac.load_sensor_on:
            v2 = 0.02

        state.vacuum.p_chamber = max(1.0e-10, float(v1))
        state.vacuum.p_load = max(1.0e-10, float(v2))

        # Für die Simulation spiegeln wir den Sensor-Schalter direkt in den Statuscode:
        # - 0: Messung "ok"
        # - 4: Sensor "off"
        state.vacuum.p_chamber_status = 0 if vac.chamber_sensor_on else 4
        state.vacuum.p_load_status = 0 if vac.load_sensor_on else 4

    def _simulate_baratron(self, state: PlantState) -> None:
        p = state.vacuum.p_chamber
        if 1.0e-4 < p < 100.0:
            baratron = p
        elif p <= 1.0e-4:
            baratron = 1.0e-4 * self._noise(0.005)
        else:
            baratron = 100.0 * self._noise(0.005)
        state.vacuum.p_baratron = float(max(1.0e-10, baratron))

    def _simulate_fug(self, state: PlantState) -> None:
        fug = state.fug
        if fug.hv_on:
            fug.current_actual = max(0.0, fug.current_set * self._noise(0.05))
            fug.voltage_actual = max(0.0, fug.voltage_set * self._noise(0.05))
        else:
            fug.current_actual = 0.0
            fug.voltage_actual = 0.0

    def _simulate_pinnacle(self, state: PlantState) -> None:
        for channel in (state.pin_a, state.pin_b):
            channel.act_pulse_frequency = channel.pulse_frequency_index * 5
            channel.act_pulse_reverse_time = channel.pulse_reverse_index * 0.1
            channel.act_regulation_mode_code = MODE_TO_REGULATION_CODE.get(channel.mode, 8)
            channel.regulation = channel.mode.value

            if channel.active:
                channel.setpoint_actual = channel.setpoint
                channel.current = max(0.0, channel.setpoint * self._noise(0.05))
                channel.voltage = max(0.0, 320.0 * self._noise(0.05))
                channel.power = channel.current * channel.voltage
            else:
                channel.setpoint_actual = 0.0
                channel.current = 0.0
                channel.voltage = 0.0
                channel.power = 0.0

    def _simulate_motors(self, state: PlantState, dt_sec: float) -> None:
        for motor in (state.motor1, state.motor2):
            key = motor.address
            if motor.running:
                if key not in self._motor_start_positions:
                    self._motor_start_positions[key] = motor.actual_position_mm

                if motor.expected_runtime_sec <= 0.0:
                    motor.expected_runtime_sec = self._estimate_motor_runtime(motor)

                motor.runtime_sec += max(0.0, dt_sec)
                motor.rest_sec = max(0.0, motor.expected_runtime_sec - motor.runtime_sec)
                progress = 1.0 if motor.expected_runtime_sec <= 0 else min(1.0, motor.runtime_sec / motor.expected_runtime_sec)

                sign = -1.0 if motor.direction == MotorDirection.RIGHT else 1.0
                delta_total = sign * abs(motor.target_position_mm) * max(1, int(motor.loops))
                start_mm = self._motor_start_positions.get(key, motor.actual_position_mm)
                motor.actual_position_mm = start_mm + progress * delta_total
                motor.encoder_position_mm = motor.actual_position_mm
                motor.status_text = "motor läuft"

                if progress >= 1.0:
                    motor.running = False
                    motor.status_text = "Steuerung bereit"
                    motor.runtime_sec = 0.0
                    motor.rest_sec = 0.0
                    self._motor_start_positions.pop(key, None)
            else:
                self._motor_start_positions.pop(key, None)

    def _simulate_argon_actual(self, state: PlantState) -> None:
        expert = state.expert
        if abs(expert.argon_set - expert.argon_set_last) > 1.0e-9:
            expert.argon_actual = max(0.0, expert.argon_set * self._noise(0.02))
            expert.argon_set_last = expert.argon_set

    def _estimate_motor_runtime(self, motor) -> float:
        speed = max(1, int(motor.target_speed))
        step_mode = max(1, int(motor.step_mode_active))
        calibration = motor.calibration if abs(motor.calibration) > 1.0e-12 else 1.0
        target_steps = abs(motor.target_position_mm) * 10000.0 * step_mode / calibration
        return float(target_steps * max(1, int(motor.loops)) / speed)

    def _noise(self, relative: float) -> float:
        return 1.0 + relative * (2.0 * self._rng.random() - 1.0)
