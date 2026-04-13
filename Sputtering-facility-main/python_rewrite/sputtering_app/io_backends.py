from __future__ import annotations

import random
from dataclasses import dataclass

from .devices.transport import (
    SerialDeviceTransport,
    SerialSettings,
    TransportError,
    list_serial_ports,
)


class BackendError(TransportError):
    pass


class SerialBackend:
    """Compatibility wrapper around the new serial transport."""

    def __init__(self) -> None:
        self._transport = SerialDeviceTransport()

    def write_read(self, port: str, baud: int, payload: bytes, timeout: float = 0.5) -> bytes:
        settings = SerialSettings(port=port, baudrate=baud, timeout=timeout)
        return self._transport.query(settings, payload, read_size=256, delay_after_write=0.05)


@dataclass
class SimBackend:
    seed: int = 12345

    def __post_init__(self) -> None:
        self._rnd = random.Random(self.seed)

    def write_read(self, port: str, baud: int, payload: bytes, timeout: float = 0.5) -> bytes:
        cmd = payload.decode("latin1", errors="ignore")
        noise = 1.0 + (self._rnd.random() - 0.5) * 0.1
        if ">M0?" in cmd:
            return f"M0:{1200.0 * noise:.1f}\r".encode()
        if ">M1?" in cmd:
            return f"M1:{0.03 * noise:.3f}\r".encode()
        if "PR1" in cmd:
            return f"{1.0e-5 * noise:.3e}\r".encode()
        if cmd.startswith("#"):
            return b"OK\r"
        return b"\x01"


__all__ = ["BackendError", "SerialBackend", "SimBackend", "list_serial_ports"]
