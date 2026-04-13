from __future__ import annotations

"""
Vakuumpumpen-Detailfenster im Stil von cdt_pressure_logger_v9.

Ziel:
- UI/Bedienlogik moeglichst nah am v9-Standalone-Tool.
- Trotzdem keine zweite serielle Direktverbindung aus diesem Fenster:
  Alle Hardwareaktionen laufen ueber den zentralen Controller.
"""

import csv
import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import matplotlib

    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

    _MATPLOTLIB_AVAILABLE = True
    _MATPLOTLIB_ERROR = ""
except Exception as exc:  # pragma: no cover - optional dependency
    _MATPLOTLIB_AVAILABLE = False
    _MATPLOTLIB_ERROR = str(exc)
    FigureCanvasTkAgg = None  # type: ignore[assignment]
    NavigationToolbar2Tk = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment]

from .models import PlantState
from .runtime_settings import RuntimeSettings

if TYPE_CHECKING:
    from .controller import Controller


STATUS_TEXT = {
    0: "ok",
    1: "underrange",
    2: "overrange",
    3: "sensor error",
    4: "sensor off",
    5: "no sensor / not identified",
    6: "identification error",
}

TPG262_INTERVAL_MAP = {"100 ms": 0.1, "1 s": 1.0, "1 min": 60.0}
MAXIGAUGE_INTERVALS = ["0.2 s", "0.5 s", "1 s", "2 s", "5 s"]
MAXIGAUGE_INTERVAL_SECONDS = {"0.2 s": 0.2, "0.5 s": 0.5, "1 s": 1.0, "2 s": 2.0, "5 s": 5.0}

UNITS = {"mbar": 0, "Torr": 1, "Pa": 2}
FILTER_MODES = {"fast": 0, "standard": 1, "medium": 1, "slow": 2}
TPG262_FSR_VALUES = {
    "0.01 mbar": 0,
    "0.1 mbar": 1,
    "1 mbar": 2,
    "10 mbar": 3,
    "100 mbar": 4,
    "1000 mbar": 5,
    "2 bar": 6,
    "5 bar": 7,
    "10 bar": 8,
    "50 bar": 9,
}
MAXIGAUGE_FSR_VALUES = {
    "1 mbar": 0,
    "10 mbar": 1,
    "100 mbar": 2,
    "1000 mbar": 3,
    "2 bar": 4,
    "5 bar": 5,
    "10 bar": 6,
    "50 bar": 7,
    "0.1 mbar": 8,
}
MAXIGAUGE_DIGITS = {"2": 2, "3": 3}

HELP_FILENAMES = {
    "diagnose": "diagnose_lesen_hilfe_vollstaendig.txt",
    "raw": "rohkommandos_pfeiffer_vollstaendig.txt",
    "unit": "hilfe_einheit.txt",
    "sensor": "hilfe_gauge_ein_aus.txt",
    "read_now": "hilfe_messwert_jetzt_lesen.txt",
    "degas": "hilfe_degas.txt",
    "activate": "hilfe_gauge_aktivieren_pruefen.txt",
    "filter": "hilfe_filter.txt",
    "calibration": "hilfe_kalibrierfaktor.txt",
    "fsr": "hilfe_fsr.txt",
    "ofc": "hilfe_ofc.txt",
    "channel_name": "hilfe_kanalname.txt",
    "digits": "hilfe_digits.txt",
    "contrast": "hilfe_contrast.txt",
    "screensave": "hilfe_screensave.txt",
}
CONFIG_FILENAME = ".cdt_pressure_logger_config.json"


def printable_status(code: int) -> str:
    return STATUS_TEXT.get(code, f"unknown ({code})")


def make_default_csv_name(device_name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = device_name.lower().replace(" ", "_")
    return str(Path.cwd() / f"{slug}_log_{ts}.csv")


def parse_csv_ints(text: str) -> List[int]:
    return [int(float(part.strip())) for part in text.split(",") if part.strip()]


def parse_csv_floats(text: str) -> List[float]:
    return [float(part.strip()) for part in text.split(",") if part.strip()]


def parse_seconds_label(text: str, default: float = 60.0) -> float:
    try:
        value = float(str(text).strip().replace(",", "."))
    except Exception:
        return default
    if value <= 0:
        return default
    return value


@dataclass
class Sample:
    t_s: float
    data: Dict[int, Tuple[int, float]]
    captured_at: float = 0.0


class BaseGaugeDriver:
    name = "Gauge"
    channel_count = 0

    def __init__(
        self,
        controller: "Controller",
        interval_label: str,
        long_term_seconds: Optional[float] = None,
    ) -> None:
        self.controller = controller
        self.interval_label = interval_label
        self.long_term_seconds = long_term_seconds
        self.io_lock = threading.RLock()

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def read_sample(self, t_s: float) -> Optional[Sample]:
        raise NotImplementedError

    def device_info_lines(self) -> List[str]:
        return []


class TPG262ControllerDriver(BaseGaugeDriver):
    name = "TPG 262"
    channel_count = 2

    def _sleep_period(self) -> float:
        if self.long_term_seconds is not None:
            return self.long_term_seconds
        return TPG262_INTERVAL_MAP.get(self.interval_label, 1.0)

    def read_sample(self, t_s: float) -> Optional[Sample]:
        d: Dict[int, Tuple[int, float]] = {}
        if self.controller.state.simulation:
            s = self.controller.state.vacuum
            d[1] = (int(s.p_chamber_status), float(s.p_chamber))
            d[2] = (int(s.p_load_status), float(s.p_load))
        else:
            with self.io_lock:
                s1, v1 = self.controller.pfeiffer_read_channel(1)
                d[1] = (int(s1), float(v1))

                runtime = self.controller.get_runtime_settings()
                if runtime.pfeiffer_single_gauge:
                    d[2] = (6, float("nan"))
                else:
                    s2, v2 = self.controller.pfeiffer_read_channel(2)
                    d[2] = (int(s2), float(v2))

        time.sleep(self._sleep_period())
        return Sample(t_s=t_s, data=d)

    def query(self, cmd: str) -> str:
        with self.io_lock:
            return self.controller.pfeiffer_query_ascii(cmd)

    def write(self, cmd: str) -> None:
        with self.io_lock:
            self.controller.pfeiffer_write_ascii(cmd)

    def get_unit(self) -> int:
        return int(self.controller.pfeiffer_get_unit())

    def set_unit(self, unit_code: int) -> None:
        self.controller.pfeiffer_set_unit(int(unit_code))

    def get_sensor_status_flags(self) -> List[int]:
        vals = self.controller.pfeiffer_get_sensor_onoff()
        if len(vals) < 2:
            raise RuntimeError(f"Unexpected SEN response: {vals}")
        return vals[:2]

    def get_error_status(self) -> str:
        return self.query("ERR")

    def reset_errors(self) -> str:
        return self.query("RES,1")

    def set_sensor_onoff(self, gauge: int, turn_on: bool) -> None:
        self.controller.pfeiffer_set_sensor_channel(int(gauge), bool(turn_on))

    def get_degas(self) -> List[int]:
        vals = parse_csv_ints(self.query("DGS"))
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected DGS response: {vals}")
        return vals

    def set_degas(self, gauge: int, on: bool) -> None:
        self.controller.pfeiffer_set_degas(int(gauge), bool(on))

    def get_filter(self) -> List[int]:
        vals = parse_csv_ints(self.query("FIL"))
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected FIL response: {vals}")
        return vals

    def set_filter(self, gauge: int, value: int) -> None:
        self.controller.pfeiffer_set_filter(int(gauge), int(value))

    def get_calibration(self) -> List[float]:
        vals = parse_csv_floats(self.query("CAL"))
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected CAL response: {vals}")
        return vals

    def set_calibration(self, gauge: int, value: float) -> None:
        self.controller.pfeiffer_set_calibration(int(gauge), float(value))

    def get_fsr(self) -> List[int]:
        vals = parse_csv_ints(self.query("FSR"))
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected FSR response: {vals}")
        return vals

    def set_fsr(self, gauge: int, value: int) -> None:
        self.controller.pfeiffer_set_fsr(int(gauge), int(value))

    def get_ofc(self) -> List[int]:
        vals = parse_csv_ints(self.query("OFC"))
        if len(vals) != 2:
            raise RuntimeError(f"Unexpected OFC response: {vals}")
        return vals

    def set_ofc(self, gauge: int, value: int) -> None:
        self.controller.pfeiffer_set_ofc(int(gauge), int(value))

    def get_ident(self) -> str:
        return self.query("TID")

    def factory_reset(self) -> None:
        self.controller.pfeiffer_factory_reset()

    def device_info_lines(self) -> List[str]:
        lines = self.controller.pfeiffer_device_info_lines()
        if lines:
            return [str(line) for line in lines]
        return ["Keine Diagnoseinformationen verfuegbar."]


class MaxiGaugeControllerDriver(BaseGaugeDriver):
    name = "MaxiGauge"
    channel_count = 6

    def _sleep_period(self) -> float:
        if self.long_term_seconds is not None:
            return self.long_term_seconds
        return MAXIGAUGE_INTERVAL_SECONDS.get(self.interval_label, 1.0)

    def read_sample(self, t_s: float) -> Optional[Sample]:
        d: Dict[int, Tuple[int, float]] = {}
        if self.controller.state.simulation:
            s = self.controller.state.vacuum
            d[1] = (int(s.p_chamber_status), float(s.p_chamber))
            d[2] = (int(s.p_load_status), float(s.p_load))
            for ch in range(3, 7):
                d[ch] = (6, float("nan"))
        else:
            with self.io_lock:
                for ch in range(1, 7):
                    try:
                        status, value = self.controller.pfeiffer_read_channel(ch)
                        d[ch] = (int(status), float(value))
                    except Exception:
                        d[ch] = (6, float("nan"))

        time.sleep(self._sleep_period())
        return Sample(t_s=t_s, data=d)

    def query(self, cmd: str) -> str:
        with self.io_lock:
            return self.controller.pfeiffer_query_ascii(cmd)

    def write(self, cmd: str) -> None:
        with self.io_lock:
            self.controller.pfeiffer_write_ascii(cmd)

    def get_unit(self) -> int:
        return int(self.controller.pfeiffer_get_unit())

    def set_unit(self, unit_code: int) -> None:
        self.controller.pfeiffer_set_unit(int(unit_code))

    def get_sensor_onoff(self) -> List[int]:
        vals = self.controller.pfeiffer_get_sensor_onoff()
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected SEN response: {vals}")
        return vals

    def set_sensor_onoff(self, gauge: int, turn_on: bool) -> None:
        self.controller.pfeiffer_set_sensor_channel(int(gauge), bool(turn_on))

    def get_degas(self) -> List[int]:
        vals = parse_csv_ints(self.query("DGS"))
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected DGS response: {vals}")
        return vals

    def set_degas(self, gauge: int, on: bool) -> None:
        self.controller.pfeiffer_set_degas(int(gauge), bool(on))

    def get_filter(self) -> List[int]:
        vals = parse_csv_ints(self.query("FIL"))
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected FIL response: {vals}")
        return vals

    def set_filter(self, gauge: int, value: int) -> None:
        self.controller.pfeiffer_set_filter(int(gauge), int(value))

    def get_calibration(self, gauge: int) -> float:
        return float(self.query(f"CA{int(gauge)}"))

    def set_calibration(self, gauge: int, value: float) -> None:
        self.controller.pfeiffer_set_calibration(int(gauge), float(value))

    def get_ofc(self) -> List[int]:
        vals = parse_csv_ints(self.query("OFC"))
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected OFC response: {vals}")
        return vals

    def set_ofc(self, gauge: int, value: int) -> None:
        self.controller.pfeiffer_set_ofc(int(gauge), int(value))

    def get_fsr(self) -> List[int]:
        vals = parse_csv_ints(self.query("FSR"))
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected FSR response: {vals}")
        return vals

    def set_fsr(self, gauge: int, value: int) -> None:
        self.controller.pfeiffer_set_fsr(int(gauge), int(value))

    def get_channel_names(self) -> List[str]:
        vals = [p.strip() for p in self.query("CID").split(",")]
        if len(vals) != 6:
            raise RuntimeError(f"Unexpected CID response: {vals}")
        return vals

    def set_channel_name(self, gauge: int, name: str) -> None:
        self.controller.pfeiffer_set_channel_name(int(gauge), str(name))

    def get_digits(self) -> int:
        return int(float(self.query("DCD")))

    def set_digits(self, value: int) -> None:
        self.controller.pfeiffer_set_digits(int(value))

    def get_contrast(self) -> int:
        return int(float(self.query("DCC")))

    def set_contrast(self, value: int) -> None:
        self.controller.pfeiffer_set_contrast(int(value))

    def get_screensave(self) -> int:
        return int(float(self.query("DCS")))

    def set_screensave(self, value: int) -> None:
        self.controller.pfeiffer_set_screensave(int(value))

    def factory_reset(self) -> None:
        self.controller.pfeiffer_factory_reset()

    def device_info_lines(self) -> List[str]:
        lines = self.controller.pfeiffer_device_info_lines()
        if lines:
            return [str(line) for line in lines]
        return ["Keine Diagnoseinformationen verfuegbar."]


class VacuumPumpWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        controller: "Controller",
        *,
        get_runtime_settings: Callable[[], RuntimeSettings] | None = None,
        apply_runtime_settings: Callable[[RuntimeSettings], None] | None = None,
        list_serial_ports_cb: Callable[[], List[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._get_runtime_settings = get_runtime_settings
        self._apply_runtime_settings = apply_runtime_settings
        self._list_serial_ports_cb = list_serial_ports_cb

        self.title("CDT pressure logger")
        self.geometry("1380x860")
        self.minsize(1180, 720)

        self.connected = False
        self.driver: Optional[BaseGaugeDriver] = None
        self.running = False
        self.logging_enabled = False
        self.reader_thread: Optional[threading.Thread] = None
        self.queue: queue.Queue = queue.Queue()

        self.csv_file = None
        self.csv_writer = None
        self.start_time: Optional[float] = None
        self.log_start_time: Optional[float] = None

        self.live_times: List[float] = []
        self.live_values: Dict[int, List[float]] = {}

        runtime = self._runtime_settings_snapshot()
        self.mode_var = tk.StringVar(value="simulation" if runtime.simulation else "real")
        self._mode_internal_change = False
        self.mode_state_var = tk.StringVar(value="")
        self.device_var = tk.StringVar(value="MaxiGauge" if runtime.pfeiffer_controller == "maxigauge" else "TPG 262")
        self.port_var = tk.StringVar(value=runtime.ports.get("dualg", ""))
        self.interval_var = tk.StringVar(value="1 s")
        self.csv_path_var = tk.StringVar(value=make_default_csv_name(self.device_var.get()))
        self.live_only_var = tk.BooleanVar(value=False)
        self.long_term_var = tk.BooleanVar(value=False)
        self.long_term_seconds_var = tk.StringVar(value="60")

        self.unit_var = tk.StringVar(value="mbar")
        self.control_channel_var = tk.StringVar(value="1")
        self.filter_var = tk.StringVar(value="standard")
        self.calibration_var = tk.StringVar(value="1.000")
        self.fsr_var = tk.StringVar(value="1000 mbar")
        self.ofc_var = tk.StringVar(value="off")
        self.channel_name_var = tk.StringVar(value="P1")
        self.maxi_digits_var = tk.StringVar(value="3")
        self.maxi_contrast_var = tk.StringVar(value="10")
        self.maxi_screensave_var = tk.StringVar(value="0")
        self.raw_command_var = tk.StringVar()
        self.control_visible_var = tk.BooleanVar(value=False)
        self.channel_display_name_var = tk.StringVar(value="")

        self.channel_names_by_device: Dict[str, Dict[int, str]] = {
            "TPG 262": {1: "Kanal 1", 2: "Kanal 2"},
            "MaxiGauge": {i: f"Kanal {i}" for i in range(1, 7)},
        }

        self._load_user_config()

        self.status_connection_var = tk.StringVar(value="Nicht verbunden")
        self.status_measurement_var = tk.StringVar(value="Nicht verbunden")
        self.status_samples_var = tk.StringVar(value="0")
        self.status_file_var = tk.StringVar(value="Keine Datei offen")
        self.channel_value_vars: Dict[int, tk.StringVar] = {i: tk.StringVar(value="-") for i in range(1, 7)}
        self.channel_status_vars: Dict[int, tk.StringVar] = {i: tk.StringVar(value="-") for i in range(1, 7)}
        self.plot_channel_vars: Dict[int, tk.BooleanVar] = {i: tk.BooleanVar(value=(i <= 2)) for i in range(1, 7)}

        self.external_plot_window = None
        self.external_fig = None
        self.external_ax = None
        self.external_canvas = None
        self.external_lines = {}

        self.logo_image = None
        self.header_cdt_logo = None
        self.logo_label = None

        self._build_ui()
        self._apply_cdt_logo()
        self.refresh_ports()
        self._apply_device_profile()
        self._update_indicators()
        self.after(200, self._poll_queue)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Runtime bridge
    # ------------------------------------------------------------------
    def _runtime_settings_snapshot(self) -> RuntimeSettings:
        if self._get_runtime_settings is not None:
            return self._get_runtime_settings()
        return self._controller.get_runtime_settings()

    def _build_runtime_from_form(self) -> RuntimeSettings:
        base = self._runtime_settings_snapshot()
        simulation = self.mode_var.get().strip().lower() == "simulation"
        backend = "maxigauge" if self.device_var.get() == "MaxiGauge" else "tpg262"

        ports = dict(base.ports)
        ports["dualg"] = self.port_var.get().strip()
        if not simulation and not ports["dualg"]:
            raise ValueError("Im Realmodus muss ein Gauge-Port gesetzt sein.")

        return RuntimeSettings(
            simulation=simulation,
            ports=ports,
            pfeiffer_controller=backend,
            pfeiffer_single_gauge=bool(base.pfeiffer_single_gauge),
            pfeiffer_maxi_chamber_channel=int(base.pfeiffer_maxi_chamber_channel),
            pfeiffer_maxi_load_channel=int(base.pfeiffer_maxi_load_channel),
        )

    def _apply_mode(self) -> None:
        if self._apply_runtime_settings is None:
            messagebox.showerror("Nicht verfuegbar", "Mode-Switch ist ohne Runtime-Callback nicht verfuegbar.", parent=self)
            return

        try:
            settings = self._build_runtime_from_form()
            self._apply_runtime_settings(settings)
            self.log_msg(f"[INFO] Modus gesetzt: {'simulation' if settings.simulation else 'real'}")
        except Exception as exc:
            messagebox.showerror("Moduswechsel fehlgeschlagen", str(exc), parent=self)
            self.log_msg(f"[ERR] Moduswechsel fehlgeschlagen: {exc}")
        finally:
            self._mode_internal_change = True
            try:
                runtime = self._runtime_settings_snapshot()
                self.mode_var.set("simulation" if runtime.simulation else "real")
            finally:
                self._mode_internal_change = False
            self._update_mode_button_style()

    def set_controller(self, controller: "Controller") -> None:
        # Wichtig bei Runtime-Wechsel:
        # Altes Monitoring sauber stoppen, damit keine stale Simulationsdaten weiterlaufen.
        self.stop_monitoring(close_csv=True, keep_time=False, quiet=True)
        self.connected = False
        self.driver = None
        self._drain_queue()

        self._controller = controller
        runtime = self._runtime_settings_snapshot()
        self._mode_internal_change = True
        self.mode_var.set("simulation" if runtime.simulation else "real")
        self._mode_internal_change = False
        self._update_mode_button_style()
        self.device_var.set("MaxiGauge" if runtime.pfeiffer_controller == "maxigauge" else "TPG 262")
        self.port_var.set(runtime.ports.get("dualg", ""))
        self._apply_device_profile()
        self.clear_plot()
        self.status_connection_var.set("Nicht verbunden")
        self.status_measurement_var.set("Nicht verbunden")
        self._update_file_status_label()
        self._update_indicators()

    def on_state_tick(self, state: PlantState) -> None:
        runtime = self._runtime_settings_snapshot()
        current_mode = "simulation" if runtime.simulation else "real"
        if self.mode_var.get() != current_mode:
            self._mode_internal_change = True
            self.mode_var.set(current_mode)
            self._mode_internal_change = False
            self._update_mode_button_style()

        if runtime.pfeiffer_controller == "maxigauge" and self.device_var.get() != "MaxiGauge":
            self.device_var.set("MaxiGauge")
            self._apply_device_profile()
        if runtime.pfeiffer_controller == "tpg262" and self.device_var.get() != "TPG 262":
            self.device_var.set("TPG 262")
            self._apply_device_profile()

        gauge_runtime = state.ports.get("dualg")

        # Harte Regel laut Anforderung:
        # In Realmodus ohne gueltige Verbindung keine simulierten Anzeigen.
        if (not state.simulation) and (gauge_runtime is None or (not gauge_runtime.connected) or gauge_runtime.failed):
            self.connected = False
            if self.running:
                self.stop_monitoring(close_csv=True, keep_time=False, quiet=True)
            self._drain_queue()
            self._clear_channel_displays()
            self.status_connection_var.set("Nicht verbunden")
            self.status_measurement_var.set("Nicht verbunden")
            self._update_file_status_label()

        self._update_indicators()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, padding=8)
        right = ttk.Frame(main, padding=8)
        main.add(left, weight=0)
        main.add(right, weight=1)

        header = ttk.Frame(left)
        header.pack(fill="x", pady=(0, 4))
        header.columnconfigure(0, weight=1)
        self.logo_label = ttk.Label(header, text="CDT pressure logger", font=("TkDefaultFont", 12, "bold"))
        self.logo_label.grid(row=0, column=0, sticky="w")

        mode_top = ttk.Frame(header)
        mode_top.grid(row=0, column=1, sticky="e")
        ttk.Label(mode_top, text="Betriebsmodus:", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.mode_light = self._create_indicator(mode_top)
        self.mode_light.grid(row=0, column=1, sticky="w", padx=(6, 8))
        mode_cb = ttk.Combobox(mode_top, textvariable=self.mode_var, state="readonly", values=["simulation", "real"], width=12)
        mode_cb.grid(row=0, column=2, sticky="w")
        mode_cb.bind("<<ComboboxSelected>>", self._on_mode_selection_changed)
        self.mode_state_label = ttk.Label(mode_top, textvariable=self.mode_state_var, width=18, anchor="w")
        self.mode_state_label.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self._update_mode_button_style()

        top = ttk.Frame(left, padding=8)
        top.pack(fill="x")
        for col in range(7):
            top.columnconfigure(col, weight=1 if col in (2, 5, 6) else 0)

        ttk.Label(top, text="Geraet:").grid(row=0, column=0, sticky="w", pady=2)
        self.conn_light = self._create_indicator(top)
        self.conn_light.grid(row=0, column=1, sticky="w", padx=(2, 6), pady=2)
        device_cb = ttk.Combobox(top, textvariable=self.device_var, state="readonly", values=["TPG 262", "MaxiGauge"], width=16)
        device_cb.grid(row=0, column=2, columnspan=2, sticky="ew", padx=(0, 10), pady=2)
        device_cb.bind("<<ComboboxSelected>>", lambda _e: self._apply_device_profile())

        ttk.Label(top, text="Port").grid(row=0, column=4, sticky="w", pady=2)
        self.port_cb = ttk.Combobox(top, textvariable=self.port_var, state="readonly", width=12)
        self.port_cb.grid(row=0, column=5, sticky="ew", padx=(4, 0), pady=2)
        self.port_cb.bind("<<ComboboxSelected>>", lambda _e: self._save_user_config())

        ttk.Button(top, text="Verbinden", command=self.connect, width=12).grid(row=1, column=0, sticky="ew", pady=3, padx=(0, 4))
        ttk.Button(top, text="Trennen", command=self.disconnect, width=12).grid(row=1, column=1, sticky="ew", pady=3, padx=(0, 4))
        ttk.Button(top, text="Aktualisieren", command=self.refresh_ports, width=12).grid(row=1, column=2, sticky="ew", pady=3, padx=(0, 4))
        ttk.Button(top, text="Diagnose", command=self.read_device_info, width=12).grid(row=1, column=3, sticky="ew", pady=3, padx=(0, 4))
        ttk.Button(top, text="Werkreset", command=self.factory_reset_device, width=12).grid(row=1, column=4, sticky="ew", pady=3, padx=(0, 4))

        ttk.Label(top, text="Messung:").grid(row=2, column=0, sticky="w", pady=(6, 2))
        self.meas_light = self._create_indicator(top)
        self.meas_light.grid(row=2, column=1, sticky="w", padx=(2, 6), pady=(6, 2))
        ttk.Label(top, textvariable=self.status_measurement_var).grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 2))
        ttk.Label(top, text="Samples:").grid(row=2, column=4, sticky="e", pady=(6, 2))
        ttk.Label(top, textvariable=self.status_samples_var).grid(row=2, column=5, sticky="w", pady=(6, 2))

        ttk.Button(top, text="Logging starten", command=self.start_measurement, width=12).grid(row=3, column=0, sticky="ew", pady=3, padx=(0, 4))
        ttk.Button(top, text="Neue Datei + Start", command=self.start_new_measurement_file, width=14).grid(row=3, column=1, sticky="ew", pady=3, padx=(0, 4))
        ttk.Button(top, text="Logging stoppen", command=self.stop_measurement, width=12).grid(row=3, column=2, sticky="ew", pady=3, padx=(0, 4))
        ttk.Checkbutton(top, text="nur live anzeigen, nicht speichern", variable=self.live_only_var, command=self._update_file_status_label).grid(row=3, column=3, columnspan=4, sticky="w", pady=3)

        self.interval_label = ttk.Label(top, text="Normal Intervall")
        self.interval_label.grid(row=4, column=0, sticky="w", pady=2)
        self.interval_cb = ttk.Combobox(top, textvariable=self.interval_var, state="readonly", width=10)
        self.interval_cb.grid(row=4, column=1, sticky="w", pady=2)
        ttk.Checkbutton(top, text="Langzeitmodus", variable=self.long_term_var).grid(row=4, column=2, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self.long_term_seconds_var, width=8).grid(row=4, column=3, sticky="w", pady=2)
        ttk.Label(top, text="s (Standard 60)").grid(row=4, column=4, sticky="w", pady=2)

        ttk.Button(top, text="Plot leeren", command=self.clear_plot, width=12).grid(row=5, column=0, sticky="ew", pady=2, padx=(0, 4))
        ttk.Button(top, text="Externer Plot", command=self.open_external_plot, width=12).grid(row=5, column=1, sticky="ew", pady=2, padx=(0, 4))
        ttk.Button(top, text="CSV plotten", command=self.plot_existing_csv, width=12).grid(row=5, column=2, sticky="ew", pady=2, padx=(0, 4))

        ttk.Label(top, text="CSV").grid(row=6, column=0, sticky="w", pady=2)
        self.csv_entry = ttk.Entry(top, textvariable=self.csv_path_var)
        self.csv_entry.grid(row=6, column=1, columnspan=6, sticky="ew", pady=2)
        ttk.Button(top, text="Durchsuchen", command=self.choose_csv_path, width=12).grid(row=7, column=0, sticky="ew", pady=2, padx=(0, 4))
        self.file_light = self._create_indicator(top)
        self.file_light.grid(row=7, column=1, sticky="w", padx=(2, 6), pady=2)
        ttk.Label(top, text="Datei:").grid(row=7, column=2, sticky="e", pady=2)
        ttk.Label(top, textvariable=self.status_file_var, wraplength=350).grid(row=7, column=3, columnspan=4, sticky="w", pady=2)

        channels = ttk.Frame(left)
        channels.pack(fill="x", pady=(8, 0))
        channels.columnconfigure(0, weight=1)
        channels.columnconfigure(1, weight=1)
        self.channel_cards = {}
        for idx, ch in enumerate((1, 2)):
            card = ttk.LabelFrame(channels, text=f"Kanal {ch}", padding=10)
            card.grid(row=0, column=idx, sticky="nsew", padx=(0, 6) if idx == 0 else (6, 0))
            card.columnconfigure(0, weight=1)
            value_lbl = ttk.Label(card, textvariable=self.channel_value_vars[ch], font=("TkDefaultFont", 30, "bold"))
            value_lbl.grid(row=0, column=0, columnspan=6, sticky="w", pady=(2, 4))
            status_lbl = ttk.Label(card, textvariable=self.channel_status_vars[ch], font=("TkDefaultFont", 12, "bold"))
            status_lbl.grid(row=1, column=0, columnspan=6, sticky="w", pady=(0, 8))
            ttk.Label(card, text="OK").grid(row=2, column=0, sticky="w")
            ok_light = self._create_indicator(card)
            ok_light.grid(row=2, column=1, sticky="w", padx=(2, 12))
            ttk.Label(card, text="AUS").grid(row=2, column=2, sticky="w")
            off_light = self._create_indicator(card)
            off_light.grid(row=2, column=3, sticky="w", padx=(2, 12))
            ttk.Label(card, text="OR").grid(row=2, column=4, sticky="w")
            or_light = self._create_indicator(card)
            or_light.grid(row=2, column=5, sticky="w", padx=(2, 0))
            self.channel_cards[ch] = {"ok": ok_light, "off": off_light, "or": or_light, "frame": card}

        plotsel = ttk.Frame(left)
        plotsel.pack(fill="x", pady=(6, 0))
        ttk.Label(plotsel, text="Im Plot anzeigen:").pack(side="left")
        self.plot_checkbuttons = {}
        for ch in range(1, 7):
            cb = ttk.Checkbutton(plotsel, text=str(ch), variable=self.plot_channel_vars[ch], command=self._apply_plot_visibility)
            cb.pack(side="left", padx=2)
            self.plot_checkbuttons[ch] = cb

        self.control_toggle_button = ttk.Button(left, text="Steuerung / Parameter einblenden", command=self._toggle_control_panel)
        self.control_toggle_button.pack(fill="x", pady=(8, 0))

        self.control_outer = ttk.LabelFrame(left, text="Steuerung / Parameter", padding=8)
        self.control_outer.pack(fill="x", pady=(6, 0))
        self.control_outer.pack_forget()
        ctrl = self.control_outer
        for col in range(5):
            ctrl.columnconfigure(col, weight=1 if col == 2 else 0)

        ttk.Label(ctrl, text="Kanal").grid(row=0, column=0, sticky="w", pady=3)
        self.control_channel_cb = ttk.Combobox(ctrl, textvariable=self.control_channel_var, state="readonly", width=8)
        self.control_channel_cb.grid(row=0, column=1, sticky="w", pady=3)
        self.control_channel_cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_selected_channel_name_input())
        ttk.Button(ctrl, text="Gauge EIN", command=lambda: self.set_sensor_state(True)).grid(row=0, column=2, sticky="ew", padx=4, pady=3)
        ttk.Button(ctrl, text="Gauge AUS", command=lambda: self.set_sensor_state(False)).grid(row=0, column=3, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 0, 4, "sensor", "Hilfe: Gauge ein/aus").grid(row=0, column=4, sticky="w")

        ttk.Label(ctrl, text="Einheit").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(ctrl, textvariable=self.unit_var, state="readonly", width=12, values=list(UNITS.keys())).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Button(ctrl, text="Einheit setzen", command=self.set_unit).grid(row=1, column=2, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 1, 4, "unit", "Hilfe: Einheit").grid(row=1, column=4, sticky="w")

        ttk.Button(ctrl, text="Messwert jetzt lesen", command=self.read_single_channel_now).grid(row=2, column=2, sticky="ew", padx=4, pady=3)
        ttk.Button(ctrl, text="Gauge aktivieren + pruefen", command=self.activate_and_verify).grid(row=2, column=3, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 2, 4, "activate", "Hilfe: Gauge aktivieren + pruefen").grid(row=2, column=4, sticky="w")

        ttk.Button(ctrl, text="Degas EIN", command=lambda: self.set_degas(True)).grid(row=3, column=2, sticky="ew", padx=4, pady=3)
        ttk.Button(ctrl, text="Degas AUS", command=lambda: self.set_degas(False)).grid(row=3, column=3, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 3, 4, "degas", "Hilfe: Degas").grid(row=3, column=4, sticky="w")

        ttk.Label(ctrl, text="Filter").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(ctrl, textvariable=self.filter_var, state="readonly", width=12, values=["fast", "standard", "slow"]).grid(row=4, column=1, sticky="w", pady=3)
        ttk.Button(ctrl, text="Filter setzen", command=self.set_filter).grid(row=4, column=2, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 4, 4, "filter", "Hilfe: Filter").grid(row=4, column=4, sticky="w")

        ttk.Label(ctrl, text="Kalibrierfaktor").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(ctrl, textvariable=self.calibration_var, width=12).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Button(ctrl, text="CAL setzen", command=self.set_calibration).grid(row=5, column=2, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 5, 4, "calibration", "Hilfe: Kalibrierfaktor").grid(row=5, column=4, sticky="w")

        ttk.Label(ctrl, text="Full Scale").grid(row=6, column=0, sticky="w", pady=3)
        self.fsr_cb = ttk.Combobox(ctrl, textvariable=self.fsr_var, state="readonly", width=12)
        self.fsr_cb.grid(row=6, column=1, sticky="w", pady=3)
        ttk.Button(ctrl, text="FSR setzen", command=self.set_fsr).grid(row=6, column=2, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 6, 4, "fsr", "Hilfe: Full Scale / FSR").grid(row=6, column=4, sticky="w")

        ttk.Label(ctrl, text="Offset-Korr.").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Combobox(ctrl, textvariable=self.ofc_var, state="readonly", width=12, values=["off", "on", "auto"]).grid(row=7, column=1, sticky="w", pady=3)
        ttk.Button(ctrl, text="OFC setzen", command=self.set_ofc).grid(row=7, column=2, sticky="ew", padx=4, pady=3)
        self._info_button(ctrl, 7, 4, "ofc", "Hilfe: Offset-Korrektur").grid(row=7, column=4, sticky="w")

        self.maxi_extra_widgets = []
        ttk.Label(ctrl, text="Kanalname (Anzeige)").grid(row=8, column=0, sticky="w", pady=3)
        self.maxi_channel_name_entry = ttk.Entry(ctrl, textvariable=self.channel_name_var, width=12)
        self.maxi_channel_name_entry.grid(row=8, column=1, sticky="w", pady=3)
        self.maxi_channel_name_btn = ttk.Button(ctrl, text="Name setzen", command=self.set_channel_name)
        self.maxi_channel_name_btn.grid(row=8, column=2, sticky="ew", padx=4, pady=3)
        self.maxi_channel_name_info = self._info_button(ctrl, 8, 4, "channel_name", "Hilfe: Kanalname")
        self.maxi_channel_name_info.grid(row=8, column=4, sticky="w")

        ttk.Label(ctrl, text="Digits").grid(row=9, column=0, sticky="w", pady=3)
        self.maxi_digits_cb = ttk.Combobox(ctrl, textvariable=self.maxi_digits_var, state="readonly", width=12, values=list(MAXIGAUGE_DIGITS.keys()))
        self.maxi_digits_cb.grid(row=9, column=1, sticky="w", pady=3)
        self.maxi_digits_btn = ttk.Button(ctrl, text="Digits setzen", command=self.set_maxi_digits)
        self.maxi_digits_btn.grid(row=9, column=2, sticky="ew", padx=4, pady=3)
        self.maxi_digits_info = self._info_button(ctrl, 9, 4, "digits", "Hilfe: Digits")
        self.maxi_digits_info.grid(row=9, column=4, sticky="w")
        self.maxi_extra_widgets.extend([self.maxi_digits_cb, self.maxi_digits_btn, self.maxi_digits_info])

        ttk.Label(ctrl, text="Contrast").grid(row=10, column=0, sticky="w", pady=3)
        self.maxi_contrast_entry = ttk.Entry(ctrl, textvariable=self.maxi_contrast_var, width=12)
        self.maxi_contrast_entry.grid(row=10, column=1, sticky="w", pady=3)
        self.maxi_contrast_btn = ttk.Button(ctrl, text="Contrast setzen", command=self.set_maxi_contrast)
        self.maxi_contrast_btn.grid(row=10, column=2, sticky="ew", padx=4, pady=3)
        self.maxi_contrast_info = self._info_button(ctrl, 10, 4, "contrast", "Hilfe: Contrast")
        self.maxi_contrast_info.grid(row=10, column=4, sticky="w")
        self.maxi_extra_widgets.extend([self.maxi_contrast_entry, self.maxi_contrast_btn, self.maxi_contrast_info])

        ttk.Label(ctrl, text="Screensave [h]").grid(row=11, column=0, sticky="w", pady=3)
        self.maxi_screensave_entry = ttk.Entry(ctrl, textvariable=self.maxi_screensave_var, width=12)
        self.maxi_screensave_entry.grid(row=11, column=1, sticky="w", pady=3)
        self.maxi_screensave_btn = ttk.Button(ctrl, text="Screensave setzen", command=self.set_maxi_screensave)
        self.maxi_screensave_btn.grid(row=11, column=2, sticky="ew", padx=4, pady=3)
        self.maxi_screensave_info = self._info_button(ctrl, 11, 4, "screensave", "Hilfe: Screensave")
        self.maxi_screensave_info.grid(row=11, column=4, sticky="w")
        self.maxi_extra_widgets.extend([self.maxi_screensave_entry, self.maxi_screensave_btn, self.maxi_screensave_info])

        ttk.Label(ctrl, text="Anzeigename").grid(row=12, column=0, sticky="w", pady=3)
        self.display_name_entry = ttk.Entry(ctrl, textvariable=self.channel_display_name_var, width=12)
        self.display_name_entry.grid(row=12, column=1, sticky="w", pady=3)
        self.display_name_entry.bind("<Return>", lambda _e: self.set_display_channel_name())
        ttk.Button(ctrl, text="Namen speichern", command=self.set_display_channel_name).grid(row=12, column=2, sticky="ew", padx=4, pady=3)

        msgf = ttk.LabelFrame(left, text="Meldungen", padding=6)
        msgf.pack(fill="x", pady=(8, 0))
        self.msg_text = tk.Text(msgf, height=5, width=54, wrap="word")
        msg_scroll = ttk.Scrollbar(msgf, orient="vertical", command=self.msg_text.yview)
        self.msg_text.configure(yscrollcommand=msg_scroll.set)
        self.msg_text.pack(side="left", fill="both", expand=True)
        msg_scroll.pack(side="right", fill="y")

        rawf = ttk.LabelFrame(left, text="Rohkommando", padding=6)
        rawf.pack(fill="x", pady=(8, 0))
        rawf.columnconfigure(0, weight=1)
        ttk.Entry(rawf, textvariable=self.raw_command_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(rawf, text="Senden", command=self.send_raw_command, width=10).grid(row=0, column=1, padx=(6, 4))
        self._info_button(rawf, 0, 2, "raw", "Hilfe: Rohkommandos").grid(row=0, column=2, sticky="w")

        plot_frame = ttk.Frame(right)
        plot_frame.pack(fill="both", expand=True)

        if _MATPLOTLIB_AVAILABLE:
            self.fig = Figure(figsize=(8.5, 6.5), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.ax.set_yscale("log")
            self.ax.set_xlabel("Zeit seit Messstart [s]")
            self.ax.set_ylabel("Druck")
            self.ax.grid(True, which="both", alpha=0.4)

            self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)

            toolbar_frame = ttk.Frame(right)
            toolbar_frame.pack(fill="x")
            self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame, pack_toolbar=False)
            self.toolbar.update()
            self.toolbar.pack(side="left", fill="x")
        else:
            self.fig = None
            self.ax = None
            self.canvas = None
            self.toolbar = None
            ttk.Label(
                plot_frame,
                text=(
                    "Matplotlib nicht verfuegbar. Plot deaktiviert.\n"
                    f"Fehler: {_MATPLOTLIB_ERROR}"
                ),
                justify="left",
            ).pack(anchor="w", fill="x", padx=10, pady=10)

        self.lines = {}
        self._rebuild_lines()

    # ------------------------------------------------------------------
    # Help/config
    # ------------------------------------------------------------------
    def _help_path(self, key: str) -> Optional[Path]:
        filename = HELP_FILENAMES[key]
        local = Path(__file__).with_name("texts") / filename
        if local.exists():
            return local
        ext = Path("/Users/francis/Vakuum-Pfeiffer-Gauges-/texts") / filename
        if ext.exists():
            return ext
        return None

    def _config_path(self) -> Path:
        return Path(__file__).with_name(CONFIG_FILENAME)

    def _load_user_config(self) -> None:
        path = self._config_path()
        if not path.exists():
            return
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return

        last_device = cfg.get("last_device")
        if last_device in ("TPG 262", "MaxiGauge"):
            self.device_var.set(last_device)

        last_port = cfg.get("last_port")
        if isinstance(last_port, str):
            self.port_var.set(last_port)

        names_cfg = cfg.get("channel_names", {})
        if isinstance(names_cfg, dict):
            for dev in ("TPG 262", "MaxiGauge"):
                dev_map = names_cfg.get(dev, {})
                if not isinstance(dev_map, dict):
                    continue
                for ch_s, label in dev_map.items():
                    try:
                        ch = int(ch_s)
                    except Exception:
                        continue
                    if isinstance(label, str) and label.strip():
                        self.channel_names_by_device.setdefault(dev, {})[ch] = label.strip()

    def _save_user_config(self) -> None:
        cfg = {
            "last_device": self.device_var.get(),
            "last_port": self.port_var.get(),
            "channel_names": {
                dev: {str(ch): label for ch, label in names.items()}
                for dev, names in self.channel_names_by_device.items()
            },
        }
        try:
            self._config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _channel_display_name(self, ch: int) -> str:
        dev = self.device_var.get()
        return self.channel_names_by_device.get(dev, {}).get(ch, f"Kanal {ch}")

    def _apply_channel_labels(self) -> None:
        for ch in range(1, 7):
            label = self._channel_display_name(ch)
            if ch in self.channel_cards:
                self.channel_cards[ch]["frame"].configure(text=f"Kanal {ch} - {label}")
        for ch, line in self.lines.items():
            line.set_label(f"Kanal {ch} - {self._channel_display_name(ch)}")
        self._apply_plot_visibility(redraw=True)

    def show_help_file(self, key: str, title: str) -> None:
        path = self._help_path(key)
        if path is None:
            content = (
                "Die Hilfedatei wurde nicht gefunden.\n\n"
                "Erwartete Orte:\n"
                f"- {Path(__file__).with_name('texts')}\n"
                "- /Users/francis/Vakuum-Pfeiffer-Gauges-/texts"
            )
        else:
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as exc:
                content = f"Die Hilfedatei konnte nicht gelesen werden:\n{path}\n\nFehler: {exc}"

        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("780x620")

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        text.insert("1.0", content)
        text.configure(state="disabled")

    def _info_button(self, parent, row: int, column: int, key: str, title: str):
        return ttk.Button(parent, text="i", width=2, command=lambda: self.show_help_file(key, title))

    def _on_mode_selection_changed(self, _event=None) -> None:
        if self._mode_internal_change:
            return
        self._apply_mode()

    def _update_mode_button_style(self) -> None:
        selected = self.mode_var.get().strip().lower()
        active = selected
        try:
            runtime = self._runtime_settings_snapshot()
            active = "simulation" if runtime.simulation else "real"
        except Exception:
            pass

        synced = selected == active
        if hasattr(self, "mode_light"):
            self._set_indicator(self.mode_light, "#2e7d32" if synced else "#f9a825")
        self.mode_state_var.set("aktiv" if synced else f"aktiv: {active}")

    def _apply_cdt_logo(self) -> None:
        icon_candidates = [
            Path(__file__).with_name("program_icon.png"),
            Path(__file__).with_name("1c4f601e-845c-4a19-968e-ffcc28512c1c.png"),
        ]
        for candidate in icon_candidates:
            if candidate.exists():
                try:
                    self.logo_image = tk.PhotoImage(file=str(candidate))
                    self.iconphoto(True, self.logo_image)
                except Exception:
                    pass
                break

        header_candidates = [
            Path(__file__).with_name("cdt_header_logo.png"),
            Path(__file__).with_name("cdt_logo_full.png"),
        ]
        for candidate in header_candidates:
            if candidate.exists():
                try:
                    self.header_cdt_logo = tk.PhotoImage(file=str(candidate))
                    if self.header_cdt_logo.width() > 220:
                        factor = max(1, self.header_cdt_logo.width() // 220)
                        self.header_cdt_logo = self.header_cdt_logo.subsample(factor, factor)
                    self.logo_label.configure(image=self.header_cdt_logo, compound="left", text=" CDT pressure logger")
                except Exception:
                    pass
                break

    # ------------------------------------------------------------------
    # Indicators/log
    # ------------------------------------------------------------------
    def _create_indicator(self, parent) -> tk.Canvas:
        canvas = tk.Canvas(parent, width=14, height=14, highlightthickness=0, bd=0)
        oval = canvas.create_oval(2, 2, 12, 12, fill="#9e9e9e", outline="#666666")
        canvas._indicator_oval = oval  # type: ignore[attr-defined]
        return canvas

    def _set_indicator(self, canvas: tk.Canvas, color: str) -> None:
        oval = getattr(canvas, "_indicator_oval", None)
        if oval is not None:
            canvas.itemconfigure(oval, fill=color)

    def _update_indicators(self) -> None:
        self._set_indicator(self.conn_light, "#2e7d32" if self.connected else "#9e9e9e")
        self._set_indicator(self.meas_light, "#2e7d32" if self.running else "#9e9e9e")
        file_active = bool(self.logging_enabled and self.csv_file is not None)
        self._set_indicator(self.file_light, "#2e7d32" if file_active else "#9e9e9e")

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def _clear_channel_displays(self) -> None:
        self.status_samples_var.set("0")
        for ch in range(1, 7):
            self.channel_value_vars[ch].set("-")
            self.channel_status_vars[ch].set("-")
            if ch in self.channel_cards:
                self._set_channel_lights(ch, 6)

    def log_msg(self, text: str) -> None:
        self.msg_text.insert("end", text + "\n")
        self.msg_text.see("end")

    # ------------------------------------------------------------------
    # Connect/monitoring
    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        ports: List[str] = []
        if self._list_serial_ports_cb is not None:
            try:
                ports = list(self._list_serial_ports_cb())
            except Exception:
                ports = []
        self.port_cb["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
        self._save_user_config()

    def _update_file_status_label(self) -> None:
        if self.logging_enabled and self.csv_file is not None:
            self.status_file_var.set(self.csv_path_var.get().strip() or "Datei offen")
        elif self.running:
            self.status_file_var.set("Monitoring ohne Dateispeicherung")
        else:
            self.status_file_var.set("Keine Datei offen")
        self._update_indicators()

    def _apply_device_profile(self) -> None:
        dev = self.device_var.get()
        self.title("CDT pressure logger")
        self.csv_path_var.set(make_default_csv_name(dev))
        active_channels = 2 if dev == "TPG 262" else 6
        channel_values = [str(i) for i in range(1, active_channels + 1)]
        self.control_channel_cb["values"] = channel_values
        if self.control_channel_var.get() not in channel_values:
            self.control_channel_var.set("1")

        if dev == "TPG 262":
            self.interval_label.config(text="Continuous Mode")
            self.interval_cb["values"] = list(TPG262_INTERVAL_MAP.keys())
            if self.interval_var.get() not in self.interval_cb["values"]:
                self.interval_var.set("1 s")
            self.fsr_cb["values"] = list(TPG262_FSR_VALUES.keys())
            for ch in range(1, 7):
                self.plot_channel_vars[ch].set(ch <= 2)
                self.plot_checkbuttons[ch].state(["disabled"] if ch > 2 else ["!disabled"])
            for widget in self.maxi_extra_widgets:
                try:
                    widget.grid_remove()
                except Exception:
                    pass
        else:
            self.interval_label.config(text="Polling-Intervall")
            self.interval_cb["values"] = MAXIGAUGE_INTERVALS
            if self.interval_var.get() not in self.interval_cb["values"]:
                self.interval_var.set("1 s")
            self.fsr_cb["values"] = list(MAXIGAUGE_FSR_VALUES.keys())
            for ch in range(1, 7):
                self.plot_checkbuttons[ch].state(["!disabled"])
                if ch > 2 and not self.plot_channel_vars[ch].get():
                    self.plot_channel_vars[ch].set(False)
            for widget in self.maxi_extra_widgets:
                try:
                    widget.grid()
                except Exception:
                    pass

        self._rebuild_lines()
        self._sync_selected_channel_name_input()
        self._apply_channel_labels()
        self.clear_plot()
        self._update_file_status_label()
        self._save_user_config()

    def _rebuild_lines(self) -> None:
        if not _MATPLOTLIB_AVAILABLE or self.ax is None or self.canvas is None:
            self.lines = {}
            return

        self.ax.clear()
        self.ax.set_yscale("log")
        self.ax.set_xlabel("Zeit seit Messstart [s]")
        self.ax.set_ylabel("Druck")
        self.ax.grid(True, which="both", alpha=0.4)
        self.lines = {}
        active_channels = 2 if self.device_var.get() == "TPG 262" else 6
        for ch in range(1, active_channels + 1):
            (line,) = self.ax.plot([], [], label=f"Kanal {ch} - {self._channel_display_name(ch)}")
            self.lines[ch] = line
        self._apply_plot_visibility(redraw=False)
        self.canvas.draw_idle()
        self._sync_external_plot(full_rebuild=True)

    def _apply_plot_visibility(self, redraw: bool = True) -> None:
        if not _MATPLOTLIB_AVAILABLE or self.ax is None:
            return

        for ch, line in self.lines.items():
            line.set_visible(self.plot_channel_vars[ch].get())
        visible_lines = [line for ch, line in self.lines.items() if self.plot_channel_vars[ch].get()]
        if visible_lines:
            self.ax.legend(handles=visible_lines, loc="best")
        else:
            legend = self.ax.get_legend()
            if legend is not None:
                legend.remove()
        if redraw and self.canvas is not None:
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()
            self._sync_external_plot(full_rebuild=True)

    def _sync_external_plot(self, full_rebuild: bool = False) -> None:
        if self.external_plot_window is None or not self.external_plot_window.winfo_exists():
            return
        if self.external_ax is None or self.external_canvas is None:
            return

        if full_rebuild:
            self.external_ax.clear()
            self.external_ax.set_yscale("log")
            self.external_ax.set_xlabel("Zeit seit Messstart [s]")
            self.external_ax.set_ylabel("Druck")
            self.external_ax.grid(True, which="both", alpha=0.4)
            self.external_lines = {}
            for ch in self.lines.keys():
                (line,) = self.external_ax.plot([], [], label=f"Kanal {ch} - {self._channel_display_name(ch)}")
                self.external_lines[ch] = line

        for ch in self.lines.keys():
            if ch in self.external_lines:
                self.external_lines[ch].set_data(self.live_times, self.live_values.get(ch, []))
                self.external_lines[ch].set_visible(self.plot_channel_vars[ch].get())

        visible_lines = [line for ch, line in self.external_lines.items() if self.plot_channel_vars.get(ch, tk.BooleanVar(value=False)).get()]
        if visible_lines:
            self.external_ax.legend(handles=visible_lines, loc="best")
        else:
            legend = self.external_ax.get_legend()
            if legend is not None:
                legend.remove()
        self.external_ax.relim()
        self.external_ax.autoscale_view()
        self.external_canvas.draw_idle()

    def open_external_plot(self) -> None:
        if not _MATPLOTLIB_AVAILABLE:
            messagebox.showerror("Nicht verfuegbar", "Matplotlib ist nicht installiert.", parent=self)
            return

        if self.external_plot_window is not None and self.external_plot_window.winfo_exists():
            self.external_plot_window.lift()
            return

        self.external_plot_window = tk.Toplevel(self)
        self.external_plot_window.title("Externer Plot")
        self.external_plot_window.geometry("950x680")

        frame = ttk.Frame(self.external_plot_window)
        frame.pack(fill="both", expand=True)

        self.external_fig = Figure(figsize=(8.5, 6), dpi=100)
        self.external_ax = self.external_fig.add_subplot(111)
        self.external_ax.set_yscale("log")
        self.external_ax.set_xlabel("Zeit seit Messstart [s]")
        self.external_ax.set_ylabel("Druck")
        self.external_ax.grid(True, which="both", alpha=0.4)

        self.external_canvas = FigureCanvasTkAgg(self.external_fig, master=frame)
        self.external_canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar_frame = ttk.Frame(frame)
        toolbar_frame.pack(fill="x")
        toolbar = NavigationToolbar2Tk(self.external_canvas, toolbar_frame, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="left", fill="x")

        self._sync_external_plot(full_rebuild=True)

    def choose_csv_path(self) -> None:
        path = filedialog.asksaveasfilename(
            title="CSV-Datei waehlen",
            defaultextension=".csv",
            filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")],
        )
        if path:
            self.csv_path_var.set(path)
            if not self.live_only_var.get() and self.csv_file is None:
                self.status_file_var.set(path)

    def new_csv_suggestion(self) -> None:
        self.csv_path_var.set(make_default_csv_name(self.device_var.get()))
        self._update_file_status_label()

    def connect(self) -> None:
        self.disconnect()

        try:
            if self._apply_runtime_settings is not None:
                self._apply_runtime_settings(self._build_runtime_from_form())
        except Exception as exc:
            messagebox.showerror("Runtime-Setup fehlgeschlagen", str(exc), parent=self)
            self.status_connection_var.set("Nicht verbunden")
            self._update_indicators()
            return

        runtime = self._runtime_settings_snapshot()

        try:
            if runtime.simulation:
                self.connected = True
                self.status_connection_var.set("Simulation aktiv")
                self.log_msg("[INFO] Simulation aktiv. Monitoring nutzt Simulationswerte.")
            else:
                ok = self._controller.reconnect_pfeiffer()
                if not ok:
                    raise RuntimeError("Gauge-Backend konnte nicht verbunden werden.")
                self.connected = True
                self.status_connection_var.set(f"Verbunden mit {runtime.ports.get('dualg', '-')}")
                self.log_msg(f"[INFO] Verbunden mit {runtime.ports.get('dualg', '-')}")

            self._save_user_config()
            self.clear_plot()
            self.start_time = None
            self.log_start_time = None
            self.logging_enabled = False
            self.start_monitoring()
        except Exception as exc:
            self.connected = False
            messagebox.showerror("Verbindung fehlgeschlagen", str(exc), parent=self)
            self.status_connection_var.set("Nicht verbunden")
            self.status_measurement_var.set("Nicht verbunden")
        finally:
            self._update_indicators()

    def disconnect(self) -> None:
        self.stop_monitoring(close_csv=True, keep_time=False, quiet=True)
        self._drain_queue()
        self.connected = False
        self.driver = None
        self._clear_channel_displays()
        self.status_connection_var.set("Nicht verbunden")
        self.status_measurement_var.set("Nicht verbunden")
        self._update_file_status_label()
        self._update_indicators()

    def _open_csv(self) -> None:
        path = self.csv_path_var.get().strip()
        if not path:
            raise RuntimeError("Kein CSV-Pfad angegeben.")
        self.csv_file = open(path, "w", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.csv_file)
        if self.device_var.get() == "TPG 262":
            header = ["t_s", "status_1", "value_1", "status_2", "value_2"]
        else:
            header = ["t_s"]
            for ch in range(1, 7):
                header += [f"status_{ch}", f"value_{ch}"]
        self.csv_writer.writerow(header)
        self.csv_file.flush()
        self.status_file_var.set(path)

    def _close_csv(self) -> None:
        if self.csv_file is not None:
            try:
                self.csv_file.close()
            except Exception:
                pass
        self.csv_file = None
        self.csv_writer = None
        self._update_file_status_label()

    def _make_driver(self) -> BaseGaugeDriver:
        long_term_seconds = parse_seconds_label(self.long_term_seconds_var.get(), default=60.0) if self.long_term_var.get() else None
        if self.device_var.get() == "TPG 262":
            return TPG262ControllerDriver(self._controller, self.interval_var.get(), long_term_seconds=long_term_seconds)
        return MaxiGaugeControllerDriver(self._controller, self.interval_var.get(), long_term_seconds=long_term_seconds)

    def start_monitoring(self, preserve_time: bool = False) -> None:
        if self.running:
            self.status_measurement_var.set("Logging laeuft" if self.logging_enabled else "Monitoring laeuft")
            self._update_indicators()
            return
        if not self.connected:
            raise RuntimeError("Nicht verbunden.")

        self.driver = self._make_driver()
        self.driver.start()
        self.running = True
        if (not preserve_time) or self.start_time is None:
            self.start_time = time.time()
        self.status_measurement_var.set("Logging laeuft" if self.logging_enabled else "Monitoring laeuft")
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self.log_msg(f"[INFO] Monitoring gestartet fuer {self.device_var.get()}")
        self._update_file_status_label()
        self._update_indicators()

    def stop_monitoring(self, close_csv: bool = True, keep_time: bool = False, quiet: bool = False) -> None:
        was_running = self.running
        self.running = False
        if self.driver is not None:
            try:
                self.driver.stop()
            except Exception:
                pass
        if self.reader_thread is not None and self.reader_thread.is_alive():
            try:
                self.reader_thread.join(timeout=1.5)
            except Exception:
                pass
        self.reader_thread = None
        if close_csv:
            self.logging_enabled = False
            self.log_start_time = None
            self._close_csv()
        if not keep_time:
            self.start_time = None
        if self.connected:
            self.status_measurement_var.set("Monitoring laeuft" if self.running else "Bereit")
        else:
            self.status_measurement_var.set("Nicht verbunden")
        if was_running and not quiet:
            self.log_msg("[INFO] Monitoring gestoppt")
        self._update_file_status_label()
        self._update_indicators()

    def start_measurement(self) -> None:
        if not self.connected:
            messagebox.showwarning("Nicht verbunden", "Bitte zuerst verbinden.", parent=self)
            return
        if self.logging_enabled and self.csv_file is not None:
            self.log_msg("[INFO] Logging laeuft bereits.")
            return
        try:
            if not self.running:
                self.start_monitoring()
            if self.live_only_var.get():
                self.logging_enabled = False
                self.log_start_time = None
                self.status_measurement_var.set("Monitoring laeuft")
                self.log_msg("[INFO] Live-Modus aktiv: keine CSV-Datei wird geschrieben.")
                self._update_file_status_label()
                return
            if self.csv_file is not None:
                self._close_csv()
            self._open_csv()
            self.logging_enabled = True
            self.log_start_time = time.time()
            self.status_measurement_var.set("Logging laeuft")
            self.log_msg(f"[INFO] Logging gestartet fuer {self.device_var.get()}")
        except Exception as exc:
            self.logging_enabled = False
            self.log_start_time = None
            self._close_csv()
            messagebox.showerror("Start fehlgeschlagen", str(exc), parent=self)
            self.status_measurement_var.set("Monitoring laeuft" if self.running else "Bereit")
        finally:
            self._update_file_status_label()
            self._update_indicators()

    def start_new_measurement_file(self) -> None:
        if not self.connected:
            messagebox.showwarning("Nicht verbunden", "Bitte zuerst verbinden.", parent=self)
            return
        if not self.live_only_var.get():
            self.csv_path_var.set(make_default_csv_name(self.device_var.get()))
        self.clear_plot()
        self.start_time = time.time()
        self.log_start_time = None
        if self.csv_file is not None:
            self._close_csv()
        self.logging_enabled = False
        if not self.running:
            try:
                self.start_monitoring(preserve_time=True)
            except Exception as exc:
                messagebox.showerror("Monitoring fehlgeschlagen", str(exc), parent=self)
                return
        self.start_measurement()

    def stop_measurement(self) -> None:
        if self.logging_enabled or self.csv_file is not None:
            self.logging_enabled = False
            self.log_start_time = None
            self._close_csv()
            self.status_measurement_var.set("Monitoring laeuft" if self.running else "Bereit")
            self.log_msg("[INFO] Logging gestoppt")
        else:
            self.status_measurement_var.set("Monitoring laeuft" if self.running else ("Bereit" if self.connected else "Nicht verbunden"))
        self._update_file_status_label()
        self._update_indicators()

    def clear_plot(self) -> None:
        self.live_times = []
        self.live_values = {ch: [] for ch in self.lines.keys()}
        for line in self.lines.values():
            line.set_data([], [])
        if _MATPLOTLIB_AVAILABLE and self.ax is not None and self.canvas is not None:
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()
            self._sync_external_plot(full_rebuild=True)
        self._clear_channel_displays()

    def _reader_loop(self) -> None:
        assert self.driver is not None
        while self.running:
            try:
                sample = self.driver.read_sample(0.0)
                if sample is not None:
                    now = time.time()
                    sample.captured_at = now
                    sample.t_s = now - self.start_time if self.start_time else 0.0
                    self.queue.put(("sample", sample))
            except Exception as exc:
                self.queue.put(("error", str(exc)))
                break

    def _write_csv_row(self, sample: Sample) -> None:
        if self.csv_writer is None or not self.logging_enabled:
            return
        if self.log_start_time is not None and sample.captured_at:
            t_csv = max(0.0, sample.captured_at - self.log_start_time)
        else:
            t_csv = sample.t_s
        row = [f"{t_csv:.3f}"]
        if self.device_var.get() == "TPG 262":
            for ch in (1, 2):
                s, v = sample.data.get(ch, (6, float("nan")))
                row += [s, f"{v:.6E}"]
        else:
            for ch in range(1, 7):
                s, v = sample.data.get(ch, (6, float("nan")))
                row += [s, f"{v:.6E}"]
        self.csv_writer.writerow(row)
        self.csv_file.flush()

    def _set_channel_lights(self, ch: int, status: int) -> None:
        if ch not in self.channel_cards:
            return
        colors = {"ok": "#9e9e9e", "off": "#9e9e9e", "or": "#9e9e9e"}
        if status == 0:
            colors["ok"] = "#2e7d32"
        elif status == 4:
            colors["off"] = "#f9a825"
        elif status in (1, 2, 3, 5, 6):
            colors["or"] = "#c62828"
        for key, canvas in self.channel_cards[ch].items():
            if key != "frame":
                self._set_indicator(canvas, colors[key])

    def _update_status_display(self, sample: Sample) -> None:
        for ch in range(1, 7):
            if ch in sample.data:
                s, v = sample.data[ch]
                self.channel_value_vars[ch].set(f"{v:.4E}" if v == v else "-")
                self.channel_status_vars[ch].set(f"{self._channel_display_name(ch)}: {printable_status(s)}")
                self._set_channel_lights(ch, s)

    def _last_positive_plot_value(self, ch: int) -> Optional[float]:
        values = self.live_values.get(ch, [])
        for v in reversed(values):
            if v == v and v > 0:
                return v
        return None

    def _status_plot_value(self, ch: int, status: int, value: float) -> float:
        if status in (5, 6):
            return float("nan")
        if value == value and value > 0:
            return value
        last_positive = self._last_positive_plot_value(ch)
        if status == 1:
            if last_positive is not None:
                return max(last_positive / 3.0, 1e-12)
            return 1e-12
        if status == 2:
            if last_positive is not None:
                return max(last_positive, 1e-12)
            return 1e3
        return float("nan")

    def _append_live_plot(self, sample: Sample) -> None:
        if not _MATPLOTLIB_AVAILABLE or self.ax is None or self.canvas is None:
            return
        if not self.live_values:
            self.live_values = {ch: [] for ch in self.lines.keys()}
        self.live_times.append(sample.t_s)
        for ch in self.lines.keys():
            s, v = sample.data.get(ch, (6, float("nan")))
            y = self._status_plot_value(ch, s, v)
            self.live_values[ch].append(y)
            self.lines[ch].set_data(self.live_times, self.live_values[ch])
            self.lines[ch].set_visible(self.plot_channel_vars[ch].get())
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()
        self._sync_external_plot(full_rebuild=False)

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "sample":
                    sample: Sample = payload
                    self._write_csv_row(sample)
                    self._update_status_display(sample)
                    self._append_live_plot(sample)
                    try:
                        count = int(self.status_samples_var.get())
                    except Exception:
                        count = 0
                    self.status_samples_var.set(str(count + 1))
                elif kind == "error":
                    self.log_msg("[ERR] " + str(payload))
                    self.stop_measurement()
                    messagebox.showerror("Messfehler", str(payload), parent=self)
        except queue.Empty:
            pass
        finally:
            self.after(200, self._poll_queue)

    # ------------------------------------------------------------------
    # Device actions (v9-like)
    # ------------------------------------------------------------------
    def _ensure_connected_driver(self) -> BaseGaugeDriver:
        if not self.connected:
            raise RuntimeError("Nicht verbunden.")
        if self.driver is None:
            self.driver = self._make_driver()
        return self.driver

    def _run_device_action(self, action: Callable[[BaseGaugeDriver], Optional[str]], restart_after: bool = True) -> None:
        was_running = self.running
        was_logging = self.logging_enabled
        command_driver: Optional[BaseGaugeDriver] = None
        if was_running:
            self.stop_monitoring(close_csv=False, keep_time=True, quiet=True)
            time.sleep(0.15)
        try:
            command_driver = self._ensure_connected_driver()
            if not was_running:
                command_driver.start()
            result = action(command_driver)
            if result:
                self.log_msg(result)
        except Exception as exc:
            messagebox.showerror("Geraetebefehl fehlgeschlagen", str(exc), parent=self)
            self.log_msg(f"[ERR] {exc}")
        finally:
            if not was_running and command_driver is not None:
                try:
                    command_driver.stop()
                except Exception:
                    pass
            if was_running and restart_after:
                try:
                    self.start_monitoring(preserve_time=True)
                    self.logging_enabled = was_logging and self.csv_file is not None
                    self.status_measurement_var.set("Logging laeuft" if self.logging_enabled else "Monitoring laeuft")
                    self._update_file_status_label()
                    self._update_indicators()
                except Exception as exc:
                    self.log_msg(f"[ERR] Monitoring konnte nach Geraetebefehl nicht neu gestartet werden: {exc}")

    def _selected_channel(self) -> int:
        return int(self.control_channel_var.get())

    def _sync_selected_channel_name_input(self) -> None:
        try:
            ch = self._selected_channel()
        except Exception:
            return
        self.channel_display_name_var.set(self._channel_display_name(ch))

    def set_display_channel_name(self) -> None:
        ch = self._selected_channel()
        dev = self.device_var.get()
        new_name = self.channel_display_name_var.get().strip() or f"Kanal {ch}"
        self.channel_names_by_device.setdefault(dev, {})[ch] = new_name
        self._apply_channel_labels()
        self._save_user_config()
        self.log_msg(f"[INFO] Anzeigename gespeichert: Kanal {ch} -> {new_name!r}")

    def set_unit(self) -> None:
        unit_code = UNITS[self.unit_var.get()]

        def action(driver: BaseGaugeDriver) -> str:
            driver.set_unit(unit_code)  # type: ignore[attr-defined]
            return f"[INFO] Einheit gesetzt auf {self.unit_var.get()}"

        self._run_device_action(action)

    def set_sensor_state(self, turn_on: bool) -> None:
        gauge = self._selected_channel()
        word = "EIN" if turn_on else "AUS"

        def action(driver: BaseGaugeDriver) -> str:
            if isinstance(driver, TPG262ControllerDriver):
                before = driver.get_sensor_status_flags()
                driver.set_sensor_onoff(gauge, turn_on)
                after = driver.get_sensor_status_flags()
                return f"[INFO] Kanal {gauge} -> Gauge {word} | SEN vorher={before} nachher={after}"
            if isinstance(driver, MaxiGaugeControllerDriver):
                before = driver.get_sensor_onoff()
                driver.set_sensor_onoff(gauge, turn_on)
                after = driver.get_sensor_onoff()
                return f"[INFO] Kanal {gauge} -> Gauge {word} | SEN vorher={before} nachher={after}"
            driver.set_sensor_onoff(gauge, turn_on)  # type: ignore[attr-defined]
            return f"[INFO] Kanal {gauge} -> Gauge {word}"

        self._run_device_action(action)

    def set_degas(self, on: bool) -> None:
        gauge = self._selected_channel()
        word = "EIN" if on else "AUS"

        def action(driver: BaseGaugeDriver) -> str:
            driver.set_degas(gauge, on)  # type: ignore[attr-defined]
            return f"[INFO] Kanal {gauge} -> Degas {word}"

        self._run_device_action(action)

    def set_filter(self) -> None:
        gauge = self._selected_channel()
        value = FILTER_MODES[self.filter_var.get()]

        def action(driver: BaseGaugeDriver) -> str:
            driver.set_filter(gauge, value)  # type: ignore[attr-defined]
            return f"[INFO] Kanal {gauge} -> Filter {self.filter_var.get()}"

        self._run_device_action(action)

    def set_calibration(self) -> None:
        gauge = self._selected_channel()
        value = float(self.calibration_var.get().replace(",", "."))

        def action(driver: BaseGaugeDriver) -> str:
            driver.set_calibration(gauge, value)  # type: ignore[attr-defined]
            return f"[INFO] Kanal {gauge} -> CAL {value:.3f}"

        self._run_device_action(action)

    def set_fsr(self) -> None:
        gauge = self._selected_channel()
        if self.device_var.get() == "TPG 262":
            value = TPG262_FSR_VALUES[self.fsr_var.get()]
        else:
            value = MAXIGAUGE_FSR_VALUES[self.fsr_var.get()]

        def action(driver: BaseGaugeDriver) -> str:
            driver.set_fsr(gauge, value)  # type: ignore[attr-defined]
            return f"[INFO] Kanal {gauge} -> FSR {self.fsr_var.get()}"

        self._run_device_action(action)

    def set_ofc(self) -> None:
        gauge = self._selected_channel()
        mapping = {"off": 0, "on": 1, "auto": 2}
        value = mapping[self.ofc_var.get()]

        def action(driver: BaseGaugeDriver) -> str:
            driver.set_ofc(gauge, value)  # type: ignore[attr-defined]
            return f"[INFO] Kanal {gauge} -> OFC {self.ofc_var.get()}"

        self._run_device_action(action)

    def set_channel_name(self) -> None:
        gauge = self._selected_channel()
        name = self.channel_name_var.get().strip() or f"Kanal {gauge}"
        dev = self.device_var.get()
        self.channel_names_by_device.setdefault(dev, {})[gauge] = name
        self._apply_channel_labels()
        self._save_user_config()
        self.log_msg(f"[INFO] Anzeigename gesetzt: Kanal {gauge} -> {name!r}")

        if self.device_var.get() == "MaxiGauge":
            def action(driver: BaseGaugeDriver) -> str:
                assert isinstance(driver, MaxiGaugeControllerDriver)
                driver.set_channel_name(gauge, name)
                return f"[INFO] MaxiGauge Hardware-Name fuer Kanal {gauge} gesetzt"

            self._run_device_action(action)

    def set_maxi_digits(self) -> None:
        if self.device_var.get() != "MaxiGauge":
            return
        value = MAXIGAUGE_DIGITS[self.maxi_digits_var.get()]

        def action(driver: BaseGaugeDriver) -> str:
            assert isinstance(driver, MaxiGaugeControllerDriver)
            driver.set_digits(value)
            return f"[INFO] MaxiGauge -> Digits {value}"

        self._run_device_action(action)

    def set_maxi_contrast(self) -> None:
        if self.device_var.get() != "MaxiGauge":
            return
        value = int(self.maxi_contrast_var.get())

        def action(driver: BaseGaugeDriver) -> str:
            assert isinstance(driver, MaxiGaugeControllerDriver)
            driver.set_contrast(value)
            return f"[INFO] MaxiGauge -> Contrast {value}"

        self._run_device_action(action)

    def set_maxi_screensave(self) -> None:
        if self.device_var.get() != "MaxiGauge":
            return
        value = int(self.maxi_screensave_var.get())

        def action(driver: BaseGaugeDriver) -> str:
            assert isinstance(driver, MaxiGaugeControllerDriver)
            driver.set_screensave(value)
            return f"[INFO] MaxiGauge -> Screensave {value} h"

        self._run_device_action(action)

    def send_raw_command(self) -> None:
        cmd = self.raw_command_var.get().strip()
        if not cmd:
            return

        def action(driver: BaseGaugeDriver) -> str:
            write_only = False
            real_cmd = cmd
            if cmd.startswith("!"):
                write_only = True
                real_cmd = cmd[1:].strip()
            if not real_cmd:
                raise RuntimeError("Leeres Rohkommando.")
            if hasattr(driver, "write") and hasattr(driver, "query"):
                if write_only:
                    driver.write(real_cmd)  # type: ignore[attr-defined]
                    return f"[RAW] {real_cmd} -> ACK"
                response = driver.query(real_cmd)  # type: ignore[attr-defined]
                return f"[RAW] {real_cmd} -> {response}"
            raise RuntimeError("Kein passender Treiber aktiv.")

        self._run_device_action(action)

    def read_single_channel_now(self) -> None:
        gauge = self._selected_channel()

        def action(driver: BaseGaugeDriver) -> str:
            if hasattr(driver, "query"):
                text = driver.query(f"PR{gauge}")  # type: ignore[attr-defined]
            else:
                raise RuntimeError("Kein passender Treiber aktiv.")
            return f"[INFO] PR{gauge} -> {text}"

        self._run_device_action(action)

    def read_device_info(self) -> None:
        def action(driver: BaseGaugeDriver) -> str:
            lines = driver.device_info_lines()
            if isinstance(driver, TPG262ControllerDriver):
                try:
                    lines.append(f"TPG 262 ERR: {driver.get_error_status()}")
                except Exception as exc:
                    lines.append(f"TPG 262 ERR: unavailable ({exc})")
            return "\n".join(f"[INFO] {line}" for line in lines)

        self._run_device_action(action)

    def activate_and_verify(self) -> None:
        gauge = self._selected_channel()

        def action(driver: BaseGaugeDriver) -> str:
            lines = []
            if isinstance(driver, TPG262ControllerDriver):
                lines.append(f"TID={driver.get_ident()}")
                lines.append(f"SEN vorher={driver.get_sensor_status_flags()}")
                try:
                    driver.set_sensor_onoff(gauge, True)
                except Exception as exc:
                    lines.append(f"Aktivierung fehlgeschlagen: {exc}")
                time.sleep(0.4)
                lines.append(f"SEN nachher={driver.get_sensor_status_flags()}")
                lines.append(f"ERR={driver.get_error_status()}")
                lines.append(f"PR{gauge}: {driver.query(f'PR{gauge}')}")
                return "[INFO] " + " | ".join(lines)
            if isinstance(driver, MaxiGaugeControllerDriver):
                lines.append(f"SEN vorher={driver.get_sensor_onoff()}")
                try:
                    driver.set_sensor_onoff(gauge, True)
                except Exception as exc:
                    lines.append(f"Aktivierung fehlgeschlagen: {exc}")
                time.sleep(0.4)
                lines.append(f"SEN nachher={driver.get_sensor_onoff()}")
                lines.append(f"PR{gauge}: {driver.query(f'PR{gauge}')}")
                return "[INFO] " + " | ".join(lines)
            return f"[INFO] Kanal {gauge}: Aktivierung versucht"

        self._run_device_action(action)

    def factory_reset_device(self) -> None:
        dev = self.device_var.get()
        if not messagebox.askyesno("Werkreset", f"Werkseinstellungen fuer {dev} laden?", parent=self):
            return

        def action(driver: BaseGaugeDriver) -> str:
            if hasattr(driver, "factory_reset"):
                driver.factory_reset()  # type: ignore[attr-defined]
                return f"[INFO] Werkreset fuer {dev} ausgeloest"
            raise RuntimeError("Werkreset fuer dieses Geraet nicht verfuegbar.")

        self._run_device_action(action)

    # ------------------------------------------------------------------
    # Misc UI actions
    # ------------------------------------------------------------------
    def _toggle_control_panel(self) -> None:
        visible = self.control_visible_var.get()
        if visible:
            self.control_outer.pack_forget()
            self.control_toggle_button.configure(text="Steuerung / Parameter einblenden")
            self.control_visible_var.set(False)
        else:
            self.control_outer.pack(fill="x", pady=(6, 0))
            self.control_toggle_button.configure(text="Steuerung / Parameter ausblenden")
            self.control_visible_var.set(True)

    def plot_existing_csv(self) -> None:
        if not _MATPLOTLIB_AVAILABLE:
            messagebox.showerror("Nicht verfuegbar", "Matplotlib ist nicht installiert.", parent=self)
            return

        path = filedialog.askopenfilename(
            title="CSV-Datei zum Plotten waehlen",
            filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                fieldnames = reader.fieldnames or []
            if not rows or not fieldnames:
                raise RuntimeError("Die CSV-Datei ist leer oder hat keine Kopfzeile.")
            time_col = fieldnames[0]
            value_cols = [name for name in fieldnames if name.startswith("value_")]
            if not value_cols:
                raise RuntimeError("Keine value_-Spalten gefunden.")

            times: List[float] = []
            values: Dict[str, List[float]] = {col: [] for col in value_cols}
            for row in rows:
                try:
                    t = float(str(row.get(time_col, "")).strip().replace(",", "."))
                except Exception:
                    continue
                times.append(t)
                for col in value_cols:
                    try:
                        values[col].append(float(str(row.get(col, "")).strip().replace(",", ".")))
                    except Exception:
                        values[col].append(float("nan"))

            if not times:
                raise RuntimeError("Keine gueltigen Zeitwerte in der CSV gefunden.")

            win = tk.Toplevel(self)
            win.title(f"CSV-Plot: {Path(path).name}")
            win.geometry("980x700")

            frame = ttk.Frame(win)
            frame.pack(fill="both", expand=True)
            fig = Figure(figsize=(8.5, 6), dpi=100)
            ax = fig.add_subplot(111)
            ax.set_yscale("log")
            ax.set_xlabel("Zeit [s]")
            ax.set_ylabel("Druck")
            ax.grid(True, which="both", alpha=0.4)
            for col in value_cols:
                ax.plot(times, values[col], label=col)
            ax.legend(loc="best")

            canvas = FigureCanvasTkAgg(fig, master=frame)
            canvas.get_tk_widget().pack(fill="both", expand=True)
            toolbar_frame = ttk.Frame(frame)
            toolbar_frame.pack(fill="x")
            toolbar = NavigationToolbar2Tk(canvas, toolbar_frame, pack_toolbar=False)
            toolbar.update()
            toolbar.pack(side="left", fill="x")
            canvas.draw_idle()
            self.log_msg(f"[INFO] CSV geplottet: {path}")
        except Exception as exc:
            messagebox.showerror("CSV-Plot fehlgeschlagen", str(exc), parent=self)
            self.log_msg(f"[ERR] CSV-Plot fehlgeschlagen: {exc}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self.stop_monitoring(close_csv=True, keep_time=False, quiet=True)
        self.destroy()

    def close_window(self) -> None:
        self._on_close()


__all__ = ["VacuumPumpWindow"]
