from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


def nanotec_cmd(address: str, body: str) -> bytes:
    return f"#{address}{body}\r".encode("ascii")


def fug_set_voltage(v: float) -> bytes:
    return f">s0{v}\n".encode("ascii")


def fug_set_voltage_ramp(v: float) -> bytes:
    return f">S0R{v}\n".encode("ascii")


def fug_set_current(i: float) -> bytes:
    return f">s1{i}\n".encode("ascii")


def fug_set_current_ramp(i: float) -> bytes:
    return f">S1R{i}\n".encode("ascii")


def fug_hv(on: bool) -> bytes:
    return b"F1\n" if on else b"F0\n"


def fug_query_voltage() -> bytes:
    return b">M0?\n"


def fug_query_current() -> bytes:
    return b">M1?\n"


# Pinnacle command ids from legacy code.
PINNACLE_CMD: Dict[str, Tuple[int, int]] = {
    "DC_OFF": (1, 0),
    "DC_ON": (2, 0),
    "REG_METHOD": (3, 1),
    "SETPOINT": (6, 2),
    "PULSE_FREQ_INDEX": (92, 1),
    "PULSE_REVERSE_TIME": (93, 1),
    "REQ_PULSE_FREQ_INDEX": (146, 0),
    "REQ_PULSE_REVERSE_TIME": (147, 0),
    "REQ_REGULATION_MODE": (154, 0),
    "REQ_SETPOINT": (164, 0),
    "REQ_ACTUAL_POWER": (165, 0),
    "REQ_ACTUAL_VOLTAGE": (166, 0),
    "REQ_ACTUAL_CURRENT": (167, 0),
    "REQ_ACTUAL_POWER_VOLTAGE_CURRENT": (168, 0),
}


@dataclass(frozen=True)
class PinnacleFrame:
    address: int
    cmd_id: int
    payload: bytes = b""


# C++ equivalent: prepare_PiNcmd().
def pinnacle_frame(address: int, cmd_id: int, payload: bytes = b"") -> bytes:
    length = len(payload)
    out = bytearray()
    out.append((address + length) & 0xFF)
    out.append(cmd_id & 0xFF)
    out.append((2 + length) & 0xFF)
    out.extend(payload)
    crc = 0
    for b in out:
        crc ^= b
    out.append(crc)
    return bytes(out)


def dualg_query_single() -> bytes:
    return b"PR1\r\n"


def dualg_query_all() -> bytes:
    return b"PRX\r\n"


def dualg_enq() -> bytes:
    return b"\x05"


def dualg_chamber_sensor_enable() -> bytes:
    return b"SEN,2,0\r\n"


def dualg_chamber_sensor_disable() -> bytes:
    return b"SEN,1,0\r\n"


def dualg_load_sensor_enable() -> bytes:
    return b"SEN,0,2\r\n"


def dualg_load_sensor_disable() -> bytes:
    return b"SEN,0,1\r\n"


def expert_toggle_cmd(addr: str, block: str, bit_value: bool) -> bytes:
    value = "01" if bit_value else "00"
    return f"#{addr}{block}{value}\r".encode("ascii")


def expert_read_outputs(addr: str) -> bytes:
    return f"${addr}6\r".encode("ascii")


def expert_handshake(addr: str) -> bytes:
    return f"${addr}F\r".encode("ascii")


def expert_write_analog_output(addr: str, value: float) -> bytes:
    return f"#{addr}+0{value:.3f}\r".encode("ascii")
