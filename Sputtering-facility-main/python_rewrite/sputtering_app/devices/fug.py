from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import protocols
from ..models import FUGState
from .transport import SerialDeviceTransport, SerialSettings, TransportError


@dataclass
class FUGDevice:
    transport: SerialDeviceTransport
    settings: SerialSettings

    def check_connection(self) -> bool:
        try:
            raw = self.transport.query(self.settings, protocols.fug_query_voltage(), read_size=100, delay_after_write=0.25)
        except TransportError:
            return False
        return self._parse_measurement(raw) is not None

    def apply_initial_settings(self, state: FUGState) -> None:
        # Mirrors FUG_ini() setup sequence from C++, but keeps commands explicit.
        self.transport.write(self.settings, protocols.fug_set_voltage(state.voltage_set))
        self.transport.write(self.settings, protocols.fug_set_voltage_ramp(state.voltage_ramp))
        self.transport.write(self.settings, protocols.fug_set_current(state.current_set))
        self.transport.write(self.settings, protocols.fug_set_current_ramp(state.current_ramp))

    def query_actuals(self, state: FUGState) -> None:
        raw_v = self.transport.query(self.settings, protocols.fug_query_voltage(), read_size=100, delay_after_write=0.05)
        raw_i = self.transport.query(self.settings, protocols.fug_query_current(), read_size=100, delay_after_write=0.05)

        parsed_v = self._parse_measurement(raw_v)
        parsed_i = self._parse_measurement(raw_i)
        if parsed_v is not None:
            state.voltage_actual = parsed_v
        if parsed_i is not None:
            state.current_actual = parsed_i

    def set_voltage_setpoint(self, state: FUGState, value: float) -> None:
        state.voltage_set = abs(float(value))
        self.transport.write(self.settings, protocols.fug_set_voltage(state.voltage_set))

    def set_current_setpoint(self, state: FUGState, value: float) -> None:
        state.current_set = abs(float(value))
        self.transport.write(self.settings, protocols.fug_set_current(state.current_set))

    def set_voltage_ramp(self, state: FUGState, value: float) -> None:
        state.voltage_ramp = abs(float(value))
        self.transport.write(self.settings, protocols.fug_set_voltage_ramp(state.voltage_ramp))

    def set_current_ramp(self, state: FUGState, value: float) -> None:
        state.current_ramp = abs(float(value))
        self.transport.write(self.settings, protocols.fug_set_current_ramp(state.current_ramp))

    def set_hv(self, state: FUGState, on: bool) -> None:
        state.hv_on = bool(on)
        self.transport.write(self.settings, protocols.fug_hv(on))

    @staticmethod
    def _parse_measurement(raw: bytes) -> Optional[float]:
        text = raw.decode("latin1", errors="ignore").strip().replace(",", ".")
        if not text:
            return None
        # Expected format is e.g. "M0:1234.5".
        if ":" in text:
            text = text.split(":", 1)[1]
        # Keep only first token in case the device adds status text.
        text = text.split()[0]
        try:
            return float(text)
        except ValueError:
            return None
