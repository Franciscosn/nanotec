from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import PROTOCOL_DIR, PROTOCOL_HEADER
from .models import PlantState


def ensure_protocol_dir() -> None:
    PROTOCOL_DIR.mkdir(parents=True, exist_ok=True)


def protocol_file_for_day(now: datetime) -> Path:
    return PROTOCOL_DIR / f"protocol_{now.day:02d}-{now.month:02d}-{now.year}.txt"


def ensure_protocol_header(path: Path) -> None:
    if path.exists():
        return
    path.write_text("\t".join(PROTOCOL_HEADER) + "\n", encoding="utf-8")


def append_protocol_row(state: PlantState, now: datetime) -> None:
    fpath = protocol_file_for_day(now)
    ensure_protocol_header(fpath)

    vat = state.vat_chamber_text()
    ar = "Ar open" if state.valves.ar_valve_open else "Ar closed"

    values = [
        now.strftime("%d.%m.%Y"),
        now.strftime("%H:%M:%S"),
        vat,
        str(state.motor1.step_mode),
        str(state.motor1.target_speed),
        f"{state.motor1.actual_position_mm:.1f}",
        str(state.motor2.step_mode),
        str(state.motor2.target_speed),
        f"{state.motor2.actual_position_mm:.1f}",
        f"{state.fug.voltage_actual:.2f}",
        f"{state.fug.current_actual:.2f}",
        str(state.pin_a.act_pulse_frequency),
        str(state.pin_a.act_regulation_mode_code),
        f"{state.pin_a.act_pulse_reverse_time:.1f}",
        f"{state.pin_a.voltage:.1f}",
        f"{state.pin_a.current:.1f}",
        f"{state.pin_a.power:.1f}",
        str(state.pin_b.act_pulse_frequency),
        str(state.pin_b.act_regulation_mode_code),
        f"{state.pin_b.act_pulse_reverse_time:.1f}",
        f"{state.pin_b.voltage:.1f}",
        f"{state.pin_b.current:.1f}",
        f"{state.pin_b.power:.1f}",
        ar,
        f"{state.vacuum.p_baratron:.3e}",
        f"{state.vacuum.p_load:.3e}",
        f"{state.vacuum.p_chamber:.3e}",
    ]

    with fpath.open("a", encoding="utf-8") as f:
        f.write("\t".join(map(str, values)) + "\n")
