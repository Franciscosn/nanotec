from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .config import (
    PFEIFFER_CONTROLLER,
    PFEIFFER_MAXI_CHAMBER_CHANNEL,
    PFEIFFER_MAXI_LOAD_CHANNEL,
    PFEIFFER_SINGLE_GAUGE,
    PORTS,
    SIMULATION,
)

_DEVICE_KEYS: tuple[str, ...] = ("nanotec", "dualg", "fug", "pinnacle", "expert")
_ALLOWED_PFEIFFER_CONTROLLERS = {"tpg262", "maxigauge"}


@dataclass(frozen=True)
class RuntimeSettings:
    simulation: bool
    ports: dict[str, str]
    pfeiffer_controller: str
    pfeiffer_single_gauge: bool
    pfeiffer_maxi_chamber_channel: int
    pfeiffer_maxi_load_channel: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation": bool(self.simulation),
            "ports": {key: self.ports.get(key, "") for key in _DEVICE_KEYS},
            "pfeiffer": {
                "controller": self.pfeiffer_controller,
                "single_gauge": bool(self.pfeiffer_single_gauge),
                "maxi_chamber_channel": int(self.pfeiffer_maxi_chamber_channel),
                "maxi_load_channel": int(self.pfeiffer_maxi_load_channel),
            },
        }

    def with_simulation(self, simulation: bool) -> "RuntimeSettings":
        return replace(self, simulation=bool(simulation))


def _clamp_channel(value: int) -> int:
    return max(1, min(6, int(value)))


def _normalize_pfeiffer_controller(value: str, default: str) -> str:
    token = str(value).strip().lower()
    if token in _ALLOWED_PFEIFFER_CONTROLLERS:
        return token
    return default


def _normalize_ports(raw_ports: Mapping[str, Any], fallback: Mapping[str, str]) -> dict[str, str]:
    ports = {key: str(fallback.get(key, "")).strip() for key in _DEVICE_KEYS}
    for key in _DEVICE_KEYS:
        if key in raw_ports and raw_ports[key] is not None:
            ports[key] = str(raw_ports[key]).strip()
    return ports


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def default_runtime_settings() -> RuntimeSettings:
    return RuntimeSettings(
        simulation=bool(SIMULATION),
        ports={key: str(PORTS.get(key, "")).strip() for key in _DEVICE_KEYS},
        pfeiffer_controller=_normalize_pfeiffer_controller(PFEIFFER_CONTROLLER, "maxigauge"),
        pfeiffer_single_gauge=bool(PFEIFFER_SINGLE_GAUGE),
        pfeiffer_maxi_chamber_channel=_clamp_channel(PFEIFFER_MAXI_CHAMBER_CHANNEL),
        pfeiffer_maxi_load_channel=_clamp_channel(PFEIFFER_MAXI_LOAD_CHANNEL),
    )


def runtime_settings_from_dict(data: Mapping[str, Any], *, base: RuntimeSettings | None = None) -> RuntimeSettings:
    base_settings = base or default_runtime_settings()

    simulation = _as_bool(data.get("simulation"), base_settings.simulation)

    raw_ports = data.get("ports")
    if isinstance(raw_ports, Mapping):
        ports = _normalize_ports(raw_ports, base_settings.ports)
    else:
        ports = dict(base_settings.ports)

    pfeiffer_data = data.get("pfeiffer") if isinstance(data.get("pfeiffer"), Mapping) else {}

    pfeiffer_controller = _normalize_pfeiffer_controller(
        str(pfeiffer_data.get("controller", data.get("pfeiffer_controller", base_settings.pfeiffer_controller))),
        base_settings.pfeiffer_controller,
    )

    single_raw = pfeiffer_data.get("single_gauge", data.get("pfeiffer_single_gauge", base_settings.pfeiffer_single_gauge))
    pfeiffer_single_gauge = _as_bool(single_raw, base_settings.pfeiffer_single_gauge)

    chamber_raw = pfeiffer_data.get(
        "maxi_chamber_channel",
        data.get("pfeiffer_maxi_chamber_channel", base_settings.pfeiffer_maxi_chamber_channel),
    )
    load_raw = pfeiffer_data.get(
        "maxi_load_channel",
        data.get("pfeiffer_maxi_load_channel", base_settings.pfeiffer_maxi_load_channel),
    )

    try:
        chamber_channel = _clamp_channel(int(chamber_raw))
    except Exception:
        chamber_channel = base_settings.pfeiffer_maxi_chamber_channel

    try:
        load_channel = _clamp_channel(int(load_raw))
    except Exception:
        load_channel = base_settings.pfeiffer_maxi_load_channel

    return RuntimeSettings(
        simulation=simulation,
        ports=ports,
        pfeiffer_controller=pfeiffer_controller,
        pfeiffer_single_gauge=pfeiffer_single_gauge,
        pfeiffer_maxi_chamber_channel=chamber_channel,
        pfeiffer_maxi_load_channel=load_channel,
    )


def load_runtime_settings(path: str | Path, *, base: RuntimeSettings | None = None) -> RuntimeSettings:
    settings_path = Path(path).expanduser().resolve()
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("Settings file must contain a JSON object.")
    return runtime_settings_from_dict(data, base=base)


def save_runtime_settings(path: str | Path, settings: RuntimeSettings) -> Path:
    settings_path = Path(path).expanduser().resolve()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings.to_dict(), indent=2) + "\n", encoding="utf-8")
    return settings_path


def find_default_settings_file(start_dir: str | Path) -> Path | None:
    """
    Sucht ein bekanntes JSON-Settings-File ab einem Startordner.

    Reihenfolge:
    1) `sputter_settings.json` im Startordner.
    2) `python_rewrite/sputter_settings.json` im Startordner.
    3) Falls Startordner `python_rewrite` ist: `../sputter_settings.json`.
    """

    root = Path(start_dir).expanduser().resolve()
    candidates = [
        root / "sputter_settings.json",
        root / "python_rewrite" / "sputter_settings.json",
    ]
    if root.name == "python_rewrite":
        candidates.append(root.parent / "sputter_settings.json")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


__all__ = [
    "RuntimeSettings",
    "default_runtime_settings",
    "find_default_settings_file",
    "load_runtime_settings",
    "runtime_settings_from_dict",
    "save_runtime_settings",
]
