from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .. import protocols
from ..models import ExpertIOState, ValveState
from .transport import SerialDeviceTransport, SerialSettings


@dataclass
class ExpertDevice:
    transport: SerialDeviceTransport
    settings: SerialSettings
    e9043_addr: str
    e9053_addr: str
    e9024_addr: str

    def check_connection(self) -> bool:
        raw = self.transport.query(
            self.settings,
            protocols.expert_handshake(self.e9053_addr),
            read_size=100,
            delay_after_write=0.02,
        )
        return len(raw) >= 5

    def tick(self, state: ExpertIOState) -> None:
        self.apply_pending_outputs(state)
        self.refresh_outputs(state)

    def apply_pending_outputs(self, state: ExpertIOState) -> None:
        self._apply_bank_changes(state, bank="A", values=state.e9043_di1, changed=state.e9043_di1_changed)
        self._apply_bank_changes(state, bank="B", values=state.e9043_di2, changed=state.e9043_di2_changed)

    def refresh_outputs(self, state: ExpertIOState) -> None:
        raw = self.transport.query(
            self.settings,
            protocols.expert_read_outputs(self.e9053_addr),
            read_size=100,
            delay_after_write=0.015,
        )
        do1, do2 = self._decode_digital_status(raw)
        state.e9053_do1[:] = do1
        state.e9053_do2[:] = do2

    def write_argon_setpoint(self, state: ExpertIOState) -> None:
        if state.argon_set == state.argon_set_last:
            return
        analog_voltage = state.argon_set * state.argon_calibration
        cmd = protocols.expert_write_analog_output(self.e9024_addr, analog_voltage)
        self.transport.query(self.settings, cmd, read_size=100, delay_after_write=0.015)
        state.argon_set_last = state.argon_set

    def _apply_bank_changes(self, state: ExpertIOState, *, bank: str, values: Iterable[int], changed: list[bool]) -> None:
        values_list = list(values)
        for idx, was_changed in enumerate(changed):
            if not was_changed:
                continue

            cmd = protocols.expert_toggle_cmd(self.e9043_addr, bank, bool(values_list[idx]))
            self.transport.query(self.settings, cmd, read_size=100, delay_after_write=0.02)
            read_raw = self.transport.query(
                self.settings,
                protocols.expert_read_outputs(self.e9043_addr),
                read_size=100,
                delay_after_write=0.02,
            )
            do1, do2 = self._decode_digital_status(read_raw)
            state.e9043_do1[:] = do1
            state.e9043_do2[:] = do2
            changed[idx] = False

    @staticmethod
    def _decode_digital_status(raw: bytes) -> tuple[list[int], list[int]]:
        text = raw.decode("latin1", errors="ignore")
        if len(text) < 5:
            return [0] * 8, [0] * 8

        b1 = ExpertDevice._nibble_to_bits(text[1])
        b2 = ExpertDevice._nibble_to_bits(text[2])
        b3 = ExpertDevice._nibble_to_bits(text[3])
        b4 = ExpertDevice._nibble_to_bits(text[4])

        do2 = [*b2, *b1]
        do1 = [*b4, *b3]
        return do1, do2

    @staticmethod
    def _nibble_to_bits(ch: str) -> list[int]:
        try:
            value = int(ch, 16)
        except ValueError:
            return [0, 0, 0, 0]
        return [
            1 if value & 0x1 else 0,
            1 if value & 0x2 else 0,
            1 if value & 0x4 else 0,
            1 if value & 0x8 else 0,
        ]


def sync_simulated_expert_outputs(state: ExpertIOState, valves: ValveState) -> None:
    """Mirror legacy simulation mappings of valve states into e9053 readback bits."""
    state.e9053_do1[2] = 1 if valves.bypass_load_open else 0
    state.e9053_do1[3] = 1 if valves.back_valve_load_open else 0
    state.e9053_do1[4] = 1 if valves.gate_load_open else 0
    state.e9053_do1[5] = 0 if valves.gate_load_open else 1
    state.e9053_do1[6] = 1 if valves.vat_load_open else 0

    state.e9053_do2[6] = 1 if valves.bypass_chamber_open else 0
    state.e9053_do2[7] = 1 if valves.back_valve_chamber_open else 0

    if valves.vat_chamber == 0:
        state.e9053_do2[0] = 1
        state.e9053_do2[1] = 0
        state.e9053_do2[2] = 0
    elif valves.vat_chamber == 1:
        state.e9053_do2[0] = 1
        state.e9053_do2[1] = 1
        state.e9053_do2[2] = 1
    else:
        state.e9053_do2[0] = 0
        state.e9053_do2[1] = 1
        state.e9053_do2[2] = 0
