from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Sequence

try:
    import serial  # type: ignore
except Exception:  # pragma: no cover
    serial = None


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baudrate: int
    parity: str = "N"
    bytesize: int = 8
    stopbits: int = 1
    timeout: float = 0.5


@dataclass(frozen=True)
class ExchangeStep:
    payload: bytes
    read_size: int = 0
    delay_after_write: float = 0.0


class SerialDeviceTransport:
    def _require_serial(self) -> None:
        if serial is None:
            raise TransportError("pyserial is not installed")

    @staticmethod
    def _parity_value(parity: str) -> str:
        p = parity.upper()
        if p == "N":
            return serial.PARITY_NONE  # type: ignore[attr-defined]
        if p == "E":
            return serial.PARITY_EVEN  # type: ignore[attr-defined]
        if p == "O":
            return serial.PARITY_ODD  # type: ignore[attr-defined]
        raise TransportError(f"unsupported parity '{parity}'")

    @staticmethod
    def _bytesize_value(bits: int) -> int:
        mapping = {
            5: serial.FIVEBITS,  # type: ignore[attr-defined]
            6: serial.SIXBITS,  # type: ignore[attr-defined]
            7: serial.SEVENBITS,  # type: ignore[attr-defined]
            8: serial.EIGHTBITS,  # type: ignore[attr-defined]
        }
        if bits not in mapping:
            raise TransportError(f"unsupported bytesize '{bits}'")
        return mapping[bits]

    @staticmethod
    def _stopbits_value(stopbits: int) -> float:
        mapping = {
            1: serial.STOPBITS_ONE,  # type: ignore[attr-defined]
            2: serial.STOPBITS_TWO,  # type: ignore[attr-defined]
        }
        if stopbits not in mapping:
            raise TransportError(f"unsupported stopbits '{stopbits}'")
        return mapping[stopbits]

    def exchange(self, settings: SerialSettings, steps: Sequence[ExchangeStep]) -> List[bytes]:
        self._require_serial()
        if not settings.port:
            raise TransportError("empty serial port")

        try:
            with serial.Serial(
                port=settings.port,
                baudrate=settings.baudrate,
                timeout=settings.timeout,
                parity=self._parity_value(settings.parity),
                bytesize=self._bytesize_value(settings.bytesize),
                stopbits=self._stopbits_value(settings.stopbits),
            ) as ser:
                responses: List[bytes] = []
                for step in steps:
                    ser.write(step.payload)
                    ser.flush()
                    if step.delay_after_write > 0:
                        time.sleep(step.delay_after_write)
                    if step.read_size > 0:
                        responses.append(ser.read(step.read_size))
                return responses
        except Exception as exc:  # pragma: no cover - hardware dependent
            raise TransportError(f"serial exchange failed on '{settings.port}': {exc}") from exc

    def query(
        self,
        settings: SerialSettings,
        payload: bytes,
        *,
        read_size: int = 256,
        delay_after_write: float = 0.05,
    ) -> bytes:
        responses = self.exchange(
            settings,
            [ExchangeStep(payload=payload, read_size=read_size, delay_after_write=delay_after_write)],
        )
        return responses[0] if responses else b""

    def write(self, settings: SerialSettings, payload: bytes, *, delay_after_write: float = 0.05) -> None:
        self.exchange(settings, [ExchangeStep(payload=payload, read_size=0, delay_after_write=delay_after_write)])


class NoopTransport:
    """Transport used in simulation mode where no serial I/O should happen."""

    def exchange(self, settings: SerialSettings, steps: Sequence[ExchangeStep]) -> List[bytes]:
        raise TransportError("serial transport disabled in simulation mode")

    def query(
        self,
        settings: SerialSettings,
        payload: bytes,
        *,
        read_size: int = 256,
        delay_after_write: float = 0.05,
    ) -> bytes:
        raise TransportError("serial transport disabled in simulation mode")

    def write(self, settings: SerialSettings, payload: bytes, *, delay_after_write: float = 0.05) -> None:
        raise TransportError("serial transport disabled in simulation mode")


def list_serial_ports() -> list[str]:
    if serial is None:
        return []
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return []
    return [p.device for p in list_ports.comports()]
