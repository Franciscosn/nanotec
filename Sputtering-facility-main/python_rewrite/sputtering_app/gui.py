from __future__ import annotations

"""
Haupt-GUI der Sputtering-Anlage.

Diese Version bildet bewusst eine "Gesamtuebersicht auf einer Seite" ab:
- Die wichtigsten Hauptprozesse (Vakuum/Gauges, Pinnacle, Nanotec, FUG, Portstatus)
  sind gleichzeitig sichtbar.
- Die wichtigsten Bedienfunktionen sind direkt im Hauptfenster verfuegbar.
- Detailfenster (`Vakuumpumpen`, `Pinnacle MDX`, `Schrittmotoren`) bleiben als
  spezialisierte Unteransichten erhalten.

Wichtige Architekturregel:
- Auch diese GUI oeffnet keine eigenen seriellen Ports.
- Alle Aktionen laufen ausschliesslich ueber den zentralen `Controller`.
"""

import math
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import TIMER_INTERVAL_SEC
from .controller import Controller
from .io_backends import list_serial_ports
from .models import MotorDirection, MotorState, PinnacleChannelState, PlantState, RegulationMode
from .nanotec_gui import NanotecWindow
from .pinnacle_gui import PinnacleWindow
from .pump_gui import VacuumPumpWindow
from .runtime_settings import RuntimeSettings, default_runtime_settings, load_runtime_settings, save_runtime_settings


class App(tk.Tk):
    """
    Oberstes Tk-Fenster der Anwendung.

    Kernaufgaben:
    1) Controller erzeugen und zyklisch ticken.
    2) Gesamtuebersicht aller Kernprozesse in einer Seite darstellen.
    3) Schnellbedienung fuer Hauptprozesse bereitstellen.
    4) Spezialisierte Unterfenster bei Bedarf oeffnen.
    """

    def __init__(
        self,
        *,
        initial_runtime: RuntimeSettings | None = None,
        runtime_path: str | Path | None = None,
    ) -> None:
        super().__init__()

        # Fenstergrundparameter:
        # Etwas groesser als vorher, damit die Gesamtuebersicht komfortabel auf
        # einem Bildschirm bedienbar bleibt.
        self.title("Sputtering Facility (Python rewrite)")
        self.geometry("1580x980")
        self.minsize(1320, 820)

        # Fruehes Logging robust machen:
        # Der Controller kann beim Start bereits Meldungen senden, bevor die
        # Listbox existiert.
        self.logbox: tk.Listbox | None = None
        self._early_log_messages: list[str] = []

        self._runtime_settings_path: Path | None = (
            Path(runtime_path).expanduser().resolve() if runtime_path is not None else None
        )
        runtime = initial_runtime or default_runtime_settings()

        # Zentraler Anlagen-Controller.
        self.ctrl = Controller(on_message=self._log, runtime=runtime)

        # Unterfenster-Referenzen.
        self._pump_window: VacuumPumpWindow | None = None
        self._pinnacle_window: PinnacleWindow | None = None
        self._nanotec_window: NanotecWindow | None = None

        # Laufzeit-StringVars fuer die Gesamtuebersicht.
        self._mode_var = tk.StringVar(value="")
        self._port_vars: dict[str, tk.StringVar] = {}

        self._vacuum_text_var = tk.StringVar(value="-")
        self._argon_text_var = tk.StringVar(value="-")
        self._valve_text_var = tk.StringVar(value="-")
        self._argon_set_var = tk.StringVar(value="0.000")

        self._fug_state_var = tk.StringVar(value="-")
        self._fug_voltage_set_var = tk.StringVar(value="1200.0")
        self._fug_current_set_var = tk.StringVar(value="0.030")
        self._fug_voltage_ramp_var = tk.StringVar(value="100.0")
        self._fug_current_ramp_var = tk.StringVar(value="0.006")

        # Schnellbedienfelder fuer Pinnacle.
        self._pin_controls: dict[str, dict[str, tk.Variable]] = {}
        self._pin_live_vars: dict[str, tk.StringVar] = {}

        # Schnellbedienfelder fuer Nanotec.
        self._motor_controls: dict[int, dict[str, tk.Variable]] = {}
        self._motor_live_vars: dict[int, tk.StringVar] = {}

        # Runtime-Konfigurationsfelder (Modus/Ports/Datei).
        self._runtime_mode_var = tk.StringVar(value="")
        self._runtime_pfeiffer_var = tk.StringVar(value="maxigauge")
        self._runtime_single_gauge_var = tk.BooleanVar(value=False)
        self._runtime_chamber_channel_var = tk.StringVar(value="1")
        self._runtime_load_channel_var = tk.StringVar(value="2")
        self._runtime_port_vars: dict[str, tk.StringVar] = {}
        self._runtime_settings_path_var = tk.StringVar(value=self._format_runtime_path(self._runtime_settings_path))

        # Grosses Anlagen-Schema (Main View).
        self._schema_canvas: tk.Canvas | None = None
        self._schema_items: dict[str, int] = {}
        self._schema_valve_led_items: dict[str, int] = {}
        self._schema_port_led_items: dict[str, int] = {}
        self._schema_motor_text_items: dict[int, int] = {}
        self._schema_power_text_items: dict[str, int] = {}
        self._schema_plot_items: dict[str, int] = {}
        self._schema_switch_buttons: dict[str, tk.Button] = {}
        self._schema_plot_bounds: tuple[float, float, float, float] = (1110.0, 235.0, 1660.0, 328.0)
        self._schema_pressure_history: deque[tuple[float, float]] = deque(maxlen=220)

        self._configure_styles()

        # Erst UI bauen, dann initiale Sollwerte aus State eintragen.
        self._build_ui()
        self._sync_runtime_form_from_settings(self.ctrl.get_runtime_settings())
        self._load_all_quick_controls_from_state()

        # Sauberer Close-Handler.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Ersten Tick planen.
        self.after(int(TIMER_INTERVAL_SEC * 1000), self._tick)

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", font=("Segoe UI", 10))
        style.configure("Headline.TLabel", font=("Segoe UI", 15, "bold"), foreground="#0e3a5b")
        style.configure("ModeBadge.TLabel", font=("Segoe UI", 10, "bold"), foreground="#0f5132")

        style.configure("Top.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#0e3a5b")
        style.configure("Runtime.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#7a2e00")
        style.configure("Ports.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#004b7c")
        style.configure("Vacuum.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#14532d")
        style.configure("Pinnacle.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#0b7285")
        style.configure("Nanotec.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#9a3412")
        style.configure("FUG.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#9f1239")
        style.configure("Log.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#334155")

        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        """
        Baut die komplette Hauptoberflaeche.

        Layout:
        - Kopfzeile mit Navigation und Schnellaktionen.
        - Runtime-Konfiguration (Simulation/Real + Ports + Datei).
        - Gesamtuebersicht (Ports + 4 Prozessbereiche).
        - Systemlog (global).
        """

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # Titelzeile mit klarer optischer Orientierung.
        title_row = ttk.Frame(root)
        title_row.pack(fill="x", pady=(0, 8))
        ttk.Label(title_row, text="Sputtering Facility Control", style="Headline.TLabel").pack(side="left")
        ttk.Label(title_row, textvariable=self._mode_var, style="ModeBadge.TLabel").pack(side="right")

        # Kopfzeile: Navigation + Schnellzugriff.
        top = ttk.LabelFrame(root, text="Navigation und Schnellaktionen", padding=8, style="Top.TLabelframe")
        top.pack(fill="x")
        top.columnconfigure(8, weight=1)

        ttk.Label(top, text="Detailansichten:").grid(row=0, column=0, sticky="w")

        ttk.Button(top, text="Vakuumpumpen (Detail)", command=self._open_pump_window).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(top, text="Pinnacle MDX (Detail)", command=self._open_pinnacle_window).grid(
            row=0, column=2, sticky="ew", padx=4
        )
        ttk.Button(top, text="Schrittmotoren (Detail)", command=self._open_nanotec_window).grid(
            row=0, column=3, sticky="ew", padx=4
        )

        ttk.Button(top, text="Argon Toggle", command=self._argon_toggle).grid(
            row=0, column=5, sticky="ew", padx=(16, 4)
        )
        ttk.Button(top, text="VAT closed", command=lambda: self.ctrl.set_vat_chamber(0)).grid(
            row=0, column=6, sticky="ew", padx=4
        )
        ttk.Button(top, text="VAT half", command=lambda: self.ctrl.set_vat_chamber(1)).grid(
            row=0, column=7, sticky="ew", padx=4
        )
        ttk.Button(top, text="VAT open", command=lambda: self.ctrl.set_vat_chamber(2)).grid(
            row=0, column=8, sticky="ew", padx=4
        )

        # Runtime-Einstellungen: Modus/Ports/Datei.
        self._build_runtime_settings_section(root).pack(fill="x", pady=(10, 0))

        # Gesamtbereich.
        overview = ttk.Frame(root)
        overview.pack(fill="both", expand=True, pady=(10, 0))
        overview.columnconfigure(0, weight=1)
        overview.rowconfigure(1, weight=1)

        # 1) Port-Status + Reconnect.
        self._build_port_section(overview).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        # 2) Hauptansichten: grosses Anlagen-Schema + bestehende Schnellkarten.
        views = ttk.Notebook(overview)
        views.grid(row=1, column=0, sticky="nsew")

        schema_tab = ttk.Frame(views, padding=(0, 2, 0, 0))
        cards_tab = ttk.Frame(views, padding=(0, 2, 0, 0))
        views.add(schema_tab, text="Anlagen-Schema")
        views.add(cards_tab, text="Schnellkarten")

        self._build_schema_section(schema_tab).pack(fill="both", expand=True)

        cards = ttk.Frame(cards_tab)
        cards.pack(fill="both", expand=True)
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        self._build_vacuum_section(cards).grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        self._build_pinnacle_section(cards).grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        self._build_nanotec_section(cards).grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self._build_fug_section(cards).grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        # 3) Globales Log.
        log_frame = ttk.LabelFrame(root, text="Systemlog", padding=6, style="Log.TLabelframe")
        log_frame.pack(fill="both", expand=False, pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.logbox = tk.Listbox(log_frame, height=9)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=log_scroll.set)
        self.logbox.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")

        self._flush_early_logs()
        self._update_schema_view(self.ctrl.state)

    def _build_runtime_settings_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(
            parent,
            text="Runtime-Konfiguration (Simulation/Real + Ports + Datei)",
            padding=8,
            style="Runtime.TLabelframe",
        )

        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        frame.columnconfigure(5, weight=1)

        ttk.Label(frame, text="Modus:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            frame,
            textvariable=self._runtime_mode_var,
            values=["Simulation", "Real hardware"],
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(frame, text="Pfeiffer-Backend:").grid(row=0, column=2, sticky="e")
        ttk.Combobox(
            frame,
            textvariable=self._runtime_pfeiffer_var,
            values=["maxigauge", "tpg262"],
            state="readonly",
            width=12,
        ).grid(row=0, column=3, sticky="w", padx=(4, 16))

        ttk.Checkbutton(
            frame,
            text="TPG262 Single-Gauge (nur PR1)",
            variable=self._runtime_single_gauge_var,
        ).grid(row=0, column=4, columnspan=2, sticky="w")

        ttk.Label(frame, text="Maxi CH Chamber:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self._runtime_chamber_channel_var, width=6).grid(
            row=1, column=1, sticky="w", padx=(4, 16), pady=(6, 0)
        )

        ttk.Label(frame, text="Maxi CH Load:").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(frame, textvariable=self._runtime_load_channel_var, width=6).grid(
            row=1, column=3, sticky="w", padx=(4, 16), pady=(6, 0)
        )

        path_row = ttk.Frame(frame)
        path_row.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        path_row.columnconfigure(1, weight=1)

        ttk.Label(path_row, text="Settings-Datei:").grid(row=0, column=0, sticky="w")
        ttk.Label(path_row, textvariable=self._runtime_settings_path_var).grid(row=0, column=1, sticky="w", padx=(6, 6))
        ttk.Button(path_row, text="Datei laden", command=self._load_runtime_settings_from_file).grid(
            row=0, column=2, sticky="ew", padx=3
        )
        ttk.Button(path_row, text="Datei speichern", command=self._save_runtime_settings_to_file).grid(
            row=0, column=3, sticky="ew", padx=3
        )

        ports = ttk.LabelFrame(frame, text="Portzuordnung", padding=6)
        ports.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        ports.columnconfigure(1, weight=1)
        ports.columnconfigure(3, weight=1)
        ports.columnconfigure(5, weight=1)
        ports.columnconfigure(7, weight=1)
        ports.columnconfigure(9, weight=1)

        for idx, key in enumerate(("dualg", "pinnacle", "nanotec", "fug", "expert")):
            var = tk.StringVar(value="")
            self._runtime_port_vars[key] = var
            col = idx * 2
            ttk.Label(ports, text=f"{key}:").grid(row=0, column=col, sticky="w", padx=(0, 4))
            ttk.Entry(ports, textvariable=var, width=20).grid(row=0, column=col + 1, sticky="ew", padx=(0, 8))

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        for i in range(4):
            btn_row.columnconfigure(i, weight=1)

        ttk.Button(btn_row, text="Serielle Ports anzeigen", command=self._show_available_ports).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(btn_row, text="Defaults laden", command=self._load_default_runtime_settings).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(
            btn_row,
            text="Controller mit diesen Settings neu starten",
            style="Primary.TButton",
            command=self._apply_runtime_settings,
        ).grid(row=0, column=2, columnspan=2, sticky="ew", padx=(4, 0))

        return frame

    def _build_port_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        """
        Baut den Portstatus-Block inkl. Reconnect-Buttons.
        """

        frame = ttk.LabelFrame(parent, text="Portstatus und Reconnect", padding=8, style="Ports.TLabelframe")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        for row, key in enumerate(("dualg", "pinnacle", "nanotec", "fug", "expert")):
            var = tk.StringVar(value=f"{key}: -")
            self._port_vars[key] = var
            ttk.Label(frame, textvariable=var).grid(row=row // 2, column=(row % 2) * 2, columnspan=2, sticky="w", padx=(0, 14), pady=1)

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        for i in range(6):
            btn_row.columnconfigure(i, weight=1)

        ttk.Button(btn_row, text="Reconnect Gauges", command=self._reconnect_gauges).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(btn_row, text="Reconnect Pinnacle", command=self._reconnect_pinnacle).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(btn_row, text="Reconnect Nanotec", command=self._reconnect_nanotec).grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(btn_row, text="Reconnect FUG", command=self._reconnect_fug).grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Button(btn_row, text="Reconnect Expert", command=self._reconnect_expert).grid(row=0, column=4, sticky="ew", padx=4)
        ttk.Button(btn_row, text="Alle Motoren STOP", command=self.ctrl.stop_all_motors).grid(row=0, column=5, sticky="ew", padx=(4, 0))

        return frame

    def _build_vacuum_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        """
        Baut die Schnellkarte fuer Vakuum/Gauges/Argon.
        """

        frame = ttk.LabelFrame(parent, text="Vakuum / Gauges / Argon", padding=10, style="Vacuum.TLabelframe")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, textvariable=self._vacuum_text_var).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ttk.Label(frame, textvariable=self._argon_text_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))
        ttk.Label(frame, textvariable=self._valve_text_var, wraplength=760).grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(0, 10),
        )

        ttk.Button(frame, text="Chamber Gauge EIN", command=lambda: self.ctrl.set_chamber_sensor(True)).grid(row=3, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(frame, text="Chamber Gauge AUS", command=lambda: self.ctrl.set_chamber_sensor(False)).grid(row=3, column=1, sticky="ew", padx=4, pady=2)
        ttk.Button(frame, text="Load Gauge EIN", command=lambda: self.ctrl.set_load_sensor(True)).grid(row=3, column=2, sticky="ew", padx=4, pady=2)
        ttk.Button(frame, text="Load Gauge AUS", command=lambda: self.ctrl.set_load_sensor(False)).grid(row=3, column=3, sticky="ew", padx=(4, 0), pady=2)

        ttk.Label(frame, text="Argon Set [sccm]:").grid(row=4, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=self._argon_set_var, width=12).grid(row=4, column=1, sticky="w", pady=(8, 2))
        ttk.Button(frame, text="Argon Set uebernehmen", command=self._apply_argon_setpoint).grid(row=4, column=2, columnspan=2, sticky="ew", padx=(4, 0), pady=(8, 2))

        ttk.Button(frame, text="Argon Toggle", command=self._argon_toggle).grid(row=5, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        ttk.Button(frame, text="VAT chamber closed", command=lambda: self.ctrl.set_vat_chamber(0)).grid(row=5, column=1, sticky="ew", padx=4, pady=(8, 0))
        ttk.Button(frame, text="VAT chamber half", command=lambda: self.ctrl.set_vat_chamber(1)).grid(row=5, column=2, sticky="ew", padx=4, pady=(8, 0))
        ttk.Button(frame, text="VAT chamber open", command=lambda: self.ctrl.set_vat_chamber(2)).grid(row=5, column=3, sticky="ew", padx=(4, 0), pady=(8, 0))

        # Ventil-Schnellsteuerung aus dem Legacy-Bedienteil:
        # Wir stellen bewusst alle Hauptpfade sichtbar bereit, damit die
        # Gesamtseite wirklich als zentrale Uebersicht nutzbar ist.
        ttk.Button(frame, text="Bypass Load Toggle", command=self.ctrl.toggle_bypass_load).grid(
            row=6,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(8, 0),
        )
        ttk.Button(frame, text="VAT Load Toggle", command=self.ctrl.toggle_vat_load).grid(
            row=6,
            column=1,
            sticky="ew",
            padx=4,
            pady=(8, 0),
        )
        ttk.Button(frame, text="Back Valve Load Toggle", command=self.ctrl.toggle_back_valve_load).grid(
            row=6,
            column=2,
            sticky="ew",
            padx=4,
            pady=(8, 0),
        )
        ttk.Button(frame, text="Gate Toggle", command=self.ctrl.toggle_gate_load).grid(
            row=6,
            column=3,
            sticky="ew",
            padx=(4, 0),
            pady=(8, 0),
        )

        ttk.Button(frame, text="Bypass Chamber Toggle", command=self.ctrl.toggle_bypass_chamber).grid(
            row=7,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(6, 0),
        )
        ttk.Button(frame, text="Back Valve Chamber Toggle", command=self.ctrl.toggle_back_valve_chamber).grid(
            row=7,
            column=1,
            sticky="ew",
            padx=4,
            pady=(6, 0),
        )
        ttk.Button(frame, text="Vakuumpumpen Detail", command=self._open_pump_window).grid(
            row=7,
            column=2,
            columnspan=2,
            sticky="ew",
            padx=(4, 0),
            pady=(6, 0),
        )

        return frame

    def _build_pinnacle_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        """
        Baut die Schnellkarte fuer Pinnacle A/B.
        """

        frame = ttk.LabelFrame(parent, text="Pinnacle Schnellsteuerung", padding=10, style="Pinnacle.TLabelframe")
        frame.columnconfigure(0, weight=1)

        for row, channel in enumerate(("A", "B")):
            ch_frame = ttk.LabelFrame(frame, text=f"Kanal {channel}", padding=8)
            ch_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            ch_frame.columnconfigure(1, weight=1)
            ch_frame.columnconfigure(5, weight=1)

            mode_var = tk.StringVar(value=RegulationMode.CURRENT.value)
            setpoint_var = tk.StringVar(value="1.0")
            freq_var = tk.StringVar(value="0")
            reverse_var = tk.StringVar(value="0.0")
            live_var = tk.StringVar(value="-")

            self._pin_controls[channel] = {
                "mode_var": mode_var,
                "setpoint_var": setpoint_var,
                "freq_var": freq_var,
                "reverse_var": reverse_var,
            }
            self._pin_live_vars[channel] = live_var

            ttk.Label(ch_frame, textvariable=live_var).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 6))

            ttk.Label(ch_frame, text="Mode:").grid(row=1, column=0, sticky="w")
            ttk.Combobox(
                ch_frame,
                textvariable=mode_var,
                values=[RegulationMode.POWER.value, RegulationMode.VOLTAGE.value, RegulationMode.CURRENT.value],
                state="readonly",
                width=10,
            ).grid(row=1, column=1, sticky="w", padx=(4, 8))

            ttk.Label(ch_frame, text="Setpoint:").grid(row=1, column=2, sticky="e")
            ttk.Entry(ch_frame, textvariable=setpoint_var, width=10).grid(row=1, column=3, sticky="w", padx=(4, 8))

            ttk.Label(ch_frame, text="Freq[kHz]:").grid(row=1, column=4, sticky="e")
            ttk.Combobox(
                ch_frame,
                textvariable=freq_var,
                values=[str(v) for v in range(0, 101, 5)],
                state="readonly",
                width=8,
            ).grid(row=1, column=5, sticky="w", padx=(4, 0))

            ttk.Label(ch_frame, text="Reverse[us]:").grid(row=2, column=0, sticky="w", pady=(6, 0))
            ttk.Combobox(
                ch_frame,
                textvariable=reverse_var,
                values=[f"{i/10:.1f}" for i in range(0, 51)],
                state="readonly",
                width=8,
            ).grid(row=2, column=1, sticky="w", padx=(4, 8), pady=(6, 0))

            ttk.Button(ch_frame, text="Apply", command=lambda ch=channel: self._apply_pinnacle_quick(ch)).grid(
                row=2, column=3, sticky="ew", padx=4, pady=(6, 0)
            )
            ttk.Button(ch_frame, text="ON", command=lambda ch=channel: self._set_pinnacle_output_from_main(ch, True)).grid(
                row=2, column=4, sticky="ew", padx=4, pady=(6, 0)
            )
            ttk.Button(ch_frame, text="OFF", command=lambda ch=channel: self._set_pinnacle_output_from_main(ch, False)).grid(
                row=2, column=5, sticky="ew", padx=(4, 0), pady=(6, 0)
            )

        footer = ttk.Frame(frame)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        footer.columnconfigure(2, weight=1)

        ttk.Button(footer, text="Sollwerte aus State laden", command=self._load_pinnacle_quick_controls_from_state).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(footer, text="NOT-AUS Pinnacle", command=self.ctrl.emergency_pinnacle_off_all).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(footer, text="Pinnacle Detailfenster", command=self._open_pinnacle_window).grid(
            row=0, column=2, sticky="ew", padx=(4, 0)
        )

        return frame

    def _build_nanotec_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        """
        Baut die Schnellkarte fuer beide Nanotec-Motoren.
        """

        frame = ttk.LabelFrame(parent, text="Nanotec Schnellsteuerung", padding=10, style="Nanotec.TLabelframe")
        frame.columnconfigure(0, weight=1)

        for row, (motor_index, state) in enumerate(((1, self.ctrl.state.motor1), (2, self.ctrl.state.motor2))):
            m_frame = ttk.LabelFrame(frame, text=f"Motor {motor_index}", padding=8)
            m_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            m_frame.columnconfigure(1, weight=1)
            m_frame.columnconfigure(5, weight=1)

            speed_var = tk.StringVar(value=str(int(state.target_speed)))
            pos_var = tk.StringVar(value=f"{float(state.target_position_mm):.3f}")
            step_var = tk.StringVar(value=str(int(state.step_mode_to_set)))
            dir_var = tk.StringVar(value=state.direction.value)
            ref_var = tk.StringVar(value=state.reference_direction.value)
            loops_var = tk.StringVar(value=str(int(state.loops)))
            live_var = tk.StringVar(value="-")

            self._motor_controls[motor_index] = {
                "speed_var": speed_var,
                "pos_var": pos_var,
                "step_var": step_var,
                "dir_var": dir_var,
                "ref_var": ref_var,
                "loops_var": loops_var,
            }
            self._motor_live_vars[motor_index] = live_var

            ttk.Label(m_frame, textvariable=live_var).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 6))

            ttk.Label(m_frame, text="Speed:").grid(row=1, column=0, sticky="w")
            ttk.Entry(m_frame, textvariable=speed_var, width=9).grid(row=1, column=1, sticky="w", padx=(4, 8))

            ttk.Label(m_frame, text="Weg[mm]:").grid(row=1, column=2, sticky="e")
            ttk.Entry(m_frame, textvariable=pos_var, width=9).grid(row=1, column=3, sticky="w", padx=(4, 8))

            ttk.Label(m_frame, text="Step:").grid(row=1, column=4, sticky="e")
            ttk.Combobox(
                m_frame,
                textvariable=step_var,
                values=["1", "2", "4", "5", "8", "10", "16", "32", "64", "254", "255"],
                state="readonly",
                width=8,
            ).grid(row=1, column=5, sticky="w", padx=(4, 0))

            ttk.Label(m_frame, text="Dir:").grid(row=2, column=0, sticky="w", pady=(6, 0))
            ttk.Combobox(
                m_frame,
                textvariable=dir_var,
                values=[MotorDirection.LEFT.value, MotorDirection.RIGHT.value],
                state="readonly",
                width=8,
            ).grid(row=2, column=1, sticky="w", padx=(4, 8), pady=(6, 0))

            ttk.Label(m_frame, text="Ref-Dir:").grid(row=2, column=2, sticky="e", pady=(6, 0))
            ttk.Combobox(
                m_frame,
                textvariable=ref_var,
                values=[MotorDirection.LEFT.value, MotorDirection.RIGHT.value],
                state="readonly",
                width=8,
            ).grid(row=2, column=3, sticky="w", padx=(4, 8), pady=(6, 0))

            ttk.Label(m_frame, text="Loops:").grid(row=2, column=4, sticky="e", pady=(6, 0))
            ttk.Entry(m_frame, textvariable=loops_var, width=8).grid(row=2, column=5, sticky="w", padx=(4, 0), pady=(6, 0))

            ttk.Button(m_frame, text="Apply", command=lambda idx=motor_index: self._apply_motor_quick(idx)).grid(
                row=3, column=0, sticky="ew", pady=(6, 0), padx=(0, 4)
            )
            ttk.Button(m_frame, text="Start", command=lambda idx=motor_index: self._start_motor_from_main(idx)).grid(
                row=3, column=1, sticky="ew", pady=(6, 0), padx=4
            )
            ttk.Button(m_frame, text="Stop", command=lambda idx=motor_index: self.ctrl.stop_motor(idx)).grid(
                row=3, column=2, sticky="ew", pady=(6, 0), padx=4
            )
            ttk.Button(m_frame, text="Referenz", command=lambda idx=motor_index: self._reference_motor_from_main(idx)).grid(
                row=3, column=3, sticky="ew", pady=(6, 0), padx=4
            )
            ttk.Button(m_frame, text="Felder aus State laden", command=lambda idx=motor_index: self._load_motor_quick_controls_from_state(idx)).grid(
                row=3, column=4, columnspan=2, sticky="ew", pady=(6, 0), padx=(4, 0)
            )

        footer = ttk.Frame(frame)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        footer.columnconfigure(2, weight=1)

        ttk.Button(footer, text="Nanotec reconnect", command=self._reconnect_nanotec).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(footer, text="Alle Motoren STOP", command=self.ctrl.stop_all_motors).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(footer, text="Nanotec Detailfenster", command=self._open_nanotec_window).grid(row=0, column=2, sticky="ew", padx=(4, 0))

        return frame

    def _build_fug_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        """
        Baut die Schnellkarte fuer das FUG-Netzteil.
        """

        frame = ttk.LabelFrame(parent, text="FUG Schnellsteuerung", padding=10, style="FUG.TLabelframe")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        ttk.Label(frame, textvariable=self._fug_state_var).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        ttk.Label(frame, text="Voltage Set [V]:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self._fug_voltage_set_var, width=10).grid(row=1, column=1, sticky="w", padx=(4, 8))
        ttk.Label(frame, text="Current Set [A]:").grid(row=1, column=2, sticky="e")
        ttk.Entry(frame, textvariable=self._fug_current_set_var, width=10).grid(row=1, column=3, sticky="w", padx=(4, 0))

        ttk.Label(frame, text="Voltage Ramp:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self._fug_voltage_ramp_var, width=10).grid(row=2, column=1, sticky="w", padx=(4, 8), pady=(6, 0))
        ttk.Label(frame, text="Current Ramp:").grid(row=2, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(frame, textvariable=self._fug_current_ramp_var, width=10).grid(row=2, column=3, sticky="w", padx=(4, 0), pady=(6, 0))

        ttk.Button(frame, text="FUG Sollwerte uebernehmen", command=self._apply_fug_quick).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0), padx=(0, 4)
        )
        ttk.Button(frame, text="HV ON", command=lambda: self.ctrl.set_fug_hv(True)).grid(
            row=3, column=2, sticky="ew", pady=(8, 0), padx=4
        )
        ttk.Button(frame, text="HV OFF", command=lambda: self.ctrl.set_fug_hv(False)).grid(
            row=3, column=3, sticky="ew", pady=(8, 0), padx=(4, 0)
        )

        ttk.Button(frame, text="FUG reconnect", command=self._reconnect_fug).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0), padx=(0, 4)
        )
        ttk.Button(frame, text="Sollwerte aus State laden", command=self._load_fug_quick_controls_from_state).grid(
            row=4, column=2, columnspan=2, sticky="ew", pady=(8, 0), padx=(4, 0)
        )

        return frame

    def _build_schema_section(self, parent: tk.Misc) -> ttk.LabelFrame:
        """
        Breite Hauptansicht als Anlagen-Schema:
        - statische Frames/Linien
        - LED-Status fuer Ventile/Ports
        - Schalterleiste fuer Kernaktionen
        - Motor/Power/Plot-Bereiche
        """

        frame = ttk.LabelFrame(parent, text="Grosses Anlagen-Schema (Main View)", padding=8, style="Top.TLabelframe")

        canvas_row = ttk.Frame(frame)
        canvas_row.pack(fill="both", expand=True)

        self._schema_canvas = tk.Canvas(
            canvas_row,
            height=370,
            bg="#f3f7fb",
            highlightthickness=1,
            highlightbackground="#aeb9c5",
        )
        self._schema_canvas.pack(fill="both", expand=True, side="top")

        xscroll = ttk.Scrollbar(canvas_row, orient="horizontal", command=self._schema_canvas.xview)
        xscroll.pack(fill="x", side="top")
        self._schema_canvas.configure(xscrollcommand=xscroll.set, scrollregion=(0, 0, 1700, 360))

        self._draw_schema_static()

        switch_row = ttk.Frame(frame)
        switch_row.pack(fill="x", pady=(8, 0))
        for i in range(10):
            switch_row.columnconfigure(i, weight=1)

        def _switch(row: int, col: int, key: str, text: str, command) -> None:
            btn = tk.Button(
                switch_row,
                text=text,
                command=command,
                font=("Segoe UI", 9, "bold"),
                relief="raised",
                bd=1,
                padx=6,
                pady=4,
                bg="#c7ced6",
                activebackground="#d3dae2",
                fg="#0b2239",
            )
            btn.grid(row=row, column=col, sticky="ew", padx=3, pady=3)
            self._schema_switch_buttons[key] = btn

        _switch(0, 0, "bypass_load", "Bypass Load", self.ctrl.toggle_bypass_load)
        _switch(0, 1, "vat_load", "VAT Load", self.ctrl.toggle_vat_load)
        _switch(0, 2, "back_load", "Back Valve Load", self.ctrl.toggle_back_valve_load)
        _switch(0, 3, "gate", "Gate", self.ctrl.toggle_gate_load)
        _switch(0, 4, "bypass_chamber", "Bypass Chamber", self.ctrl.toggle_bypass_chamber)
        _switch(0, 5, "back_chamber", "Back Valve Chamber", self.ctrl.toggle_back_valve_chamber)
        _switch(0, 6, "argon", "Argon", self._argon_toggle)
        _switch(0, 7, "vat_chamber_closed", "VAT CH Closed", lambda: self.ctrl.set_vat_chamber(0))
        _switch(0, 8, "vat_chamber_half", "VAT CH Half", lambda: self.ctrl.set_vat_chamber(1))
        _switch(0, 9, "vat_chamber_open", "VAT CH Open", lambda: self.ctrl.set_vat_chamber(2))

        aux_row = ttk.Frame(frame)
        aux_row.pack(fill="x", pady=(4, 0))
        for i in range(8):
            aux_row.columnconfigure(i, weight=1)

        ttk.Button(aux_row, text="Reconnect Gauges", command=self._reconnect_gauges).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(aux_row, text="Reconnect Expert", command=self._reconnect_expert).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(aux_row, text="Reconnect Pinnacle", command=self._reconnect_pinnacle).grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(aux_row, text="Reconnect Nanotec", command=self._reconnect_nanotec).grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Button(aux_row, text="Reconnect FUG", command=self._reconnect_fug).grid(row=0, column=4, sticky="ew", padx=4)
        ttk.Button(aux_row, text="Vakuumpumpen Detail", command=self._open_pump_window).grid(row=0, column=5, sticky="ew", padx=4)
        ttk.Button(aux_row, text="Pinnacle Detail", command=self._open_pinnacle_window).grid(row=0, column=6, sticky="ew", padx=4)
        ttk.Button(aux_row, text="Nanotec Detail", command=self._open_nanotec_window).grid(row=0, column=7, sticky="ew", padx=(4, 0))

        return frame

    def _schema_add_component(self, key: str, label: str, x: float, y: float, width: float = 130.0) -> None:
        canvas = self._schema_canvas
        if canvas is None:
            return

        canvas.create_rectangle(x, y, x + width, y + 30, outline="#7f8c99", width=1, fill="#ffffff")
        canvas.create_text(x + 8, y + 16, text=label, anchor="w", font=("Segoe UI", 8, "bold"), fill="#0b2239")
        led = canvas.create_oval(x + width - 22, y + 7, x + width - 8, y + 21, fill="#9aa4af", outline="#4d5a66")
        self._schema_valve_led_items[key] = led

    def _draw_schema_static(self) -> None:
        canvas = self._schema_canvas
        if canvas is None:
            return

        canvas.delete("all")

        canvas.create_rectangle(20, 20, 1680, 340, outline="#b2bfcc", width=2, fill="#f7fafd")
        canvas.create_text(30, 30, text="LOADLOCK", anchor="w", font=("Segoe UI", 11, "bold"), fill="#103a5d")
        canvas.create_text(575, 30, text="CHAMBER", anchor="w", font=("Segoe UI", 11, "bold"), fill="#103a5d")
        canvas.create_text(1095, 30, text="PORT & STATUS", anchor="w", font=("Segoe UI", 11, "bold"), fill="#103a5d")

        canvas.create_rectangle(40, 55, 560, 210, outline="#9fb0c0", width=1, fill="#ffffff")
        canvas.create_rectangle(580, 55, 1080, 210, outline="#9fb0c0", width=1, fill="#ffffff")
        canvas.create_rectangle(1100, 55, 1665, 210, outline="#9fb0c0", width=1, fill="#ffffff")
        canvas.create_rectangle(40, 225, 430, 335, outline="#9fb0c0", width=1, fill="#ffffff")
        canvas.create_rectangle(450, 225, 1070, 335, outline="#9fb0c0", width=1, fill="#ffffff")
        canvas.create_rectangle(1095, 225, 1665, 335, outline="#9fb0c0", width=1, fill="#ffffff")

        canvas.create_text(55, 238, text="MOTOR-BEREICH", anchor="w", font=("Segoe UI", 10, "bold"), fill="#0b2239")
        canvas.create_text(465, 238, text="POWER-BEREICH", anchor="w", font=("Segoe UI", 10, "bold"), fill="#0b2239")
        canvas.create_text(1110, 238, text="PLOT-BEREICH (Pressure Trend)", anchor="w", font=("Segoe UI", 10, "bold"), fill="#0b2239")

        # Prozesslinien (statisch aufgebaut, Farbe wird pro Tick aktualisiert).
        self._schema_items["line_back_load"] = canvas.create_line(82, 110, 248, 110, width=8, fill="#b0b8bf")
        self._schema_items["line_vat_load"] = canvas.create_line(248, 110, 405, 110, width=8, fill="#b0b8bf")
        self._schema_items["line_gate"] = canvas.create_line(405, 110, 545, 110, width=8, fill="#b0b8bf")
        self._schema_items["line_bridge"] = canvas.create_line(545, 110, 620, 110, width=8, fill="#b0b8bf")

        self._schema_items["line_back_chamber"] = canvas.create_line(620, 110, 805, 110, width=8, fill="#b0b8bf")
        self._schema_items["line_vat_chamber"] = canvas.create_line(805, 110, 1020, 110, width=8, fill="#b0b8bf")

        self._schema_items["line_bypass_load"] = canvas.create_line(82, 165, 248, 165, width=6, fill="#b0b8bf")
        self._schema_items["line_bypass_chamber"] = canvas.create_line(620, 165, 805, 165, width=6, fill="#b0b8bf")
        self._schema_items["line_argon"] = canvas.create_line(1020, 165, 1020, 120, width=6, fill="#b0b8bf")

        # Komponenten mit LED-Punkten.
        self._schema_add_component("back_load", "Back Valve Load", 78, 93)
        self._schema_add_component("vat_load", "VAT Load", 242, 93)
        self._schema_add_component("gate", "Gate Valve", 402, 93)
        self._schema_add_component("bypass_load", "Bypass Load", 78, 148)
        self._schema_add_component("sensor_load", "Load Sensor", 242, 148)

        self._schema_add_component("back_chamber", "Back Valve Chamber", 618, 93, width=150)
        self._schema_add_component("vat_chamber", "VAT Chamber", 802, 93, width=150)
        self._schema_add_component("bypass_chamber", "Bypass Chamber", 618, 148, width=150)
        self._schema_add_component("sensor_chamber", "Chamber Sensor", 802, 148, width=150)
        self._schema_add_component("argon", "Argon Valve", 962, 148, width=105)

        # Portpanel.
        port_rows = [
            ("dualg", "Gauge"),
            ("expert", "Expert I/O"),
            ("pinnacle", "Pinnacle"),
            ("nanotec", "Nanotec"),
            ("fug", "FUG"),
        ]
        for idx, (key, label) in enumerate(port_rows):
            y = 82 + idx * 24
            canvas.create_text(1120, y, text=label, anchor="w", font=("Segoe UI", 9, "bold"), fill="#0b2239")
            led = canvas.create_oval(1210, y - 7, 1224, y + 7, fill="#9aa4af", outline="#4d5a66")
            self._schema_port_led_items[key] = led
            self._schema_items[f"port_text_{key}"] = canvas.create_text(
                1232,
                y,
                text="-",
                anchor="w",
                font=("Segoe UI", 8),
                fill="#334155",
            )

        self._schema_items["vacuum_summary"] = canvas.create_text(
            1120,
            188,
            text="P chamber=-, P load=-",
            anchor="w",
            font=("Segoe UI", 9, "bold"),
            fill="#0b2239",
        )

        # Motor/Power dynamische Textfelder.
        self._schema_motor_text_items[1] = canvas.create_text(
            55,
            272,
            text="Motor 1: -",
            anchor="w",
            font=("Segoe UI", 9),
            fill="#0b2239",
        )
        self._schema_motor_text_items[2] = canvas.create_text(
            55,
            302,
            text="Motor 2: -",
            anchor="w",
            font=("Segoe UI", 9),
            fill="#0b2239",
        )

        self._schema_power_text_items["fug"] = canvas.create_text(
            465,
            272,
            text="FUG: -",
            anchor="w",
            font=("Segoe UI", 9),
            fill="#0b2239",
        )
        self._schema_power_text_items["pin_a"] = canvas.create_text(
            465,
            292,
            text="Pinnacle A: -",
            anchor="w",
            font=("Segoe UI", 9),
            fill="#0b2239",
        )
        self._schema_power_text_items["pin_b"] = canvas.create_text(
            465,
            312,
            text="Pinnacle B: -",
            anchor="w",
            font=("Segoe UI", 9),
            fill="#0b2239",
        )

        # Plotfenster.
        x0, y0, x1, y1 = self._schema_plot_bounds
        canvas.create_rectangle(x0, y0, x1, y1, outline="#8ea1b5", width=1, fill="#f9fcff")
        canvas.create_line(x0 + 30, y1 - 5, x1 - 5, y1 - 5, fill="#90a4b8", width=1)
        canvas.create_line(x0 + 30, y0 + 5, x0 + 30, y1 - 5, fill="#90a4b8", width=1)
        self._schema_plot_items["chamber_line"] = canvas.create_line(
            x0 + 30,
            y1 - 5,
            x0 + 30,
            y1 - 5,
            fill="#1c7ed6",
            width=2,
            smooth=True,
        )
        self._schema_plot_items["load_line"] = canvas.create_line(
            x0 + 30,
            y1 - 5,
            x0 + 30,
            y1 - 5,
            fill="#2f9e44",
            width=2,
            smooth=True,
        )
        self._schema_plot_items["plot_info"] = canvas.create_text(
            x0 + 8,
            y0 + 10,
            text="Chamber(blau) / Load(gruen)",
            anchor="w",
            font=("Segoe UI", 8),
            fill="#334155",
        )

    def _schema_set_led(self, key: str, color: str) -> None:
        canvas = self._schema_canvas
        item = self._schema_valve_led_items.get(key)
        if canvas is None or item is None:
            return
        canvas.itemconfigure(item, fill=color)

    def _schema_set_port_led(self, key: str, color: str, text: str) -> None:
        canvas = self._schema_canvas
        led = self._schema_port_led_items.get(key)
        text_id = self._schema_items.get(f"port_text_{key}")
        if canvas is None or led is None:
            return
        canvas.itemconfigure(led, fill=color)
        if text_id is not None:
            canvas.itemconfigure(text_id, text=text)

    def _schema_set_line(self, key: str, color: str) -> None:
        canvas = self._schema_canvas
        item = self._schema_items.get(key)
        if canvas is None or item is None:
            return
        canvas.itemconfigure(item, fill=color)

    def _schema_set_button(self, key: str, mode: str) -> None:
        btn = self._schema_switch_buttons.get(key)
        if btn is None:
            return

        token = mode.strip().lower()
        if token == "on":
            bg, fg = "#2f9e44", "#ffffff"
        elif token == "warn":
            bg, fg = "#f59f00", "#111111"
        else:
            bg, fg = "#c7ced6", "#0b2239"

        btn.configure(bg=bg, activebackground=bg, fg=fg, activeforeground=fg)

    def _update_schema_view(self, state: PlantState) -> None:
        canvas = self._schema_canvas
        if canvas is None:
            return

        c_off = "#b0b8bf"
        c_on = "#2f9e44"
        c_warn = "#f59f00"
        c_fail = "#d64545"

        # Linienzustand.
        self._schema_set_line("line_back_load", c_on if state.valves.back_valve_load_open else c_off)
        self._schema_set_line("line_vat_load", c_on if state.valves.vat_load_open else c_off)
        self._schema_set_line("line_gate", c_on if state.valves.gate_load_open else c_off)
        self._schema_set_line(
            "line_bridge",
            c_on if (state.valves.gate_load_open or state.valves.vat_load_open or state.valves.back_valve_load_open) else c_off,
        )
        self._schema_set_line("line_back_chamber", c_on if state.valves.back_valve_chamber_open else c_off)
        self._schema_set_line(
            "line_vat_chamber",
            c_on if state.valves.vat_chamber == 2 else (c_warn if state.valves.vat_chamber == 1 else c_off),
        )
        self._schema_set_line("line_bypass_load", c_on if state.valves.bypass_load_open else c_off)
        self._schema_set_line("line_bypass_chamber", c_on if state.valves.bypass_chamber_open else c_off)
        self._schema_set_line("line_argon", c_on if state.valves.ar_valve_open else c_off)

        # Ventil- und Sensor-LEDs.
        self._schema_set_led("back_load", c_on if state.valves.back_valve_load_open else c_off)
        self._schema_set_led("vat_load", c_on if state.valves.vat_load_open else c_off)
        self._schema_set_led("gate", c_on if state.valves.gate_load_open else c_off)
        self._schema_set_led("bypass_load", c_on if state.valves.bypass_load_open else c_off)
        self._schema_set_led("back_chamber", c_on if state.valves.back_valve_chamber_open else c_off)
        self._schema_set_led("vat_chamber", c_on if state.valves.vat_chamber == 2 else (c_warn if state.valves.vat_chamber == 1 else c_off))
        self._schema_set_led("bypass_chamber", c_on if state.valves.bypass_chamber_open else c_off)
        self._schema_set_led("argon", c_on if state.valves.ar_valve_open else c_off)
        self._schema_set_led("sensor_load", c_on if state.vacuum.load_sensor_on else c_off)
        self._schema_set_led("sensor_chamber", c_on if state.vacuum.chamber_sensor_on else c_off)

        # Port-LEDs.
        for key in ("dualg", "expert", "pinnacle", "nanotec", "fug"):
            runtime = state.ports.get(key)
            if runtime is None:
                self._schema_set_port_led(key, c_off, "n/a")
                continue
            if runtime.failed:
                color = c_fail
                token = "failed"
            elif runtime.connected:
                color = c_on
                token = "ok"
            else:
                color = c_off
                token = "off"
            self._schema_set_port_led(key, color, token)

        # Zusammenfassungen.
        vac_text = "P chamber={:.3e} (s{}), P load={:.3e} (s{})".format(
            state.vacuum.p_chamber,
            int(state.vacuum.p_chamber_status),
            state.vacuum.p_load,
            int(state.vacuum.p_load_status),
        )
        item = self._schema_items.get("vacuum_summary")
        if item is not None:
            canvas.itemconfigure(item, text=vac_text)

        m1_item = self._schema_motor_text_items.get(1)
        if m1_item is not None:
            canvas.itemconfigure(
                m1_item,
                text=(
                    "Motor1: conn={} run={} pos={:.2f}mm status={} ({})".format(
                        state.motor1.connected,
                        state.motor1.running,
                        float(state.motor1.actual_position_mm),
                        int(state.motor1.status_code),
                        state.motor1.status_text,
                    )
                ),
            )

        m2_item = self._schema_motor_text_items.get(2)
        if m2_item is not None:
            canvas.itemconfigure(
                m2_item,
                text=(
                    "Motor2: conn={} run={} pos={:.2f}mm status={} ({})".format(
                        state.motor2.connected,
                        state.motor2.running,
                        float(state.motor2.actual_position_mm),
                        int(state.motor2.status_code),
                        state.motor2.status_text,
                    )
                ),
            )

        fug_item = self._schema_power_text_items.get("fug")
        if fug_item is not None:
            canvas.itemconfigure(
                fug_item,
                text=(
                    "FUG: HV={} U={:.1f}V I={:.4f}A".format(
                        state.fug.hv_on,
                        float(state.fug.voltage_actual),
                        float(state.fug.current_actual),
                    )
                ),
            )

        pin_a_item = self._schema_power_text_items.get("pin_a")
        if pin_a_item is not None:
            canvas.itemconfigure(
                pin_a_item,
                text=(
                    "Pinnacle A: {} | U={:.1f}V I={:.3f}A P={:.3f}W".format(
                        "ON" if state.pin_a.active else "OFF",
                        float(state.pin_a.voltage),
                        float(state.pin_a.current),
                        float(state.pin_a.power),
                    )
                ),
            )

        pin_b_item = self._schema_power_text_items.get("pin_b")
        if pin_b_item is not None:
            canvas.itemconfigure(
                pin_b_item,
                text=(
                    "Pinnacle B: {} | U={:.1f}V I={:.3f}A P={:.3f}W".format(
                        "ON" if state.pin_b.active else "OFF",
                        float(state.pin_b.voltage),
                        float(state.pin_b.current),
                        float(state.pin_b.power),
                    )
                ),
            )

        # Schalterfarben.
        self._schema_set_button("bypass_load", "on" if state.valves.bypass_load_open else "off")
        self._schema_set_button("vat_load", "on" if state.valves.vat_load_open else "off")
        self._schema_set_button("back_load", "on" if state.valves.back_valve_load_open else "off")
        self._schema_set_button("gate", "on" if state.valves.gate_load_open else "off")
        self._schema_set_button("bypass_chamber", "on" if state.valves.bypass_chamber_open else "off")
        self._schema_set_button("back_chamber", "on" if state.valves.back_valve_chamber_open else "off")
        self._schema_set_button("argon", "on" if state.valves.ar_valve_open else "off")
        self._schema_set_button("vat_chamber_closed", "on" if state.valves.vat_chamber == 0 else "off")
        self._schema_set_button("vat_chamber_half", "warn" if state.valves.vat_chamber == 1 else "off")
        self._schema_set_button("vat_chamber_open", "on" if state.valves.vat_chamber == 2 else "off")

        self._update_schema_plot(state)

    def _update_schema_plot(self, state: PlantState) -> None:
        canvas = self._schema_canvas
        chamber_line = self._schema_plot_items.get("chamber_line")
        load_line = self._schema_plot_items.get("load_line")
        info_item = self._schema_plot_items.get("plot_info")
        if canvas is None or chamber_line is None or load_line is None:
            return

        self._schema_pressure_history.append((float(state.vacuum.p_chamber), float(state.vacuum.p_load)))

        x0, y0, x1, y1 = self._schema_plot_bounds
        left = x0 + 30
        right = x1 - 6
        top = y0 + 6
        bottom = y1 - 6

        def _pressure_to_y(value: float) -> float:
            # Logbereich -9 .. +3 (mbar) fuer stabile Uebersicht.
            p = max(1.0e-12, float(value))
            log10 = math.log10(p)
            norm = (log10 - (-9.0)) / 12.0
            norm = max(0.0, min(1.0, norm))
            return bottom - norm * (bottom - top)

        n = len(self._schema_pressure_history)
        if n < 2:
            canvas.coords(chamber_line, left, bottom, right, bottom)
            canvas.coords(load_line, left, bottom, right, bottom)
        else:
            span = max(1, n - 1)
            chamber_points: list[float] = []
            load_points: list[float] = []
            for i, (p_ch, p_ld) in enumerate(self._schema_pressure_history):
                x = left + (right - left) * (i / span)
                chamber_points.extend([x, _pressure_to_y(p_ch)])
                load_points.extend([x, _pressure_to_y(p_ld)])
            canvas.coords(chamber_line, *chamber_points)
            canvas.coords(load_line, *load_points)

        if info_item is not None:
            canvas.itemconfigure(
                info_item,
                text=(
                    "Chamber(blau)={:.2e}  |  Load(gruen)={:.2e}  [mbar, log]".format(
                        float(state.vacuum.p_chamber),
                        float(state.vacuum.p_load),
                    )
                ),
            )

    # ------------------------------------------------------------------
    # Runtime-Konfiguration (Modus / Ports / Settings-Datei)
    # ------------------------------------------------------------------
    @staticmethod
    def _format_runtime_path(path: Path | None) -> str:
        return str(path) if path is not None else "(keine Datei)"

    def _sync_runtime_form_from_settings(self, settings: RuntimeSettings) -> None:
        self._runtime_mode_var.set("Simulation" if settings.simulation else "Real hardware")
        self._runtime_pfeiffer_var.set(settings.pfeiffer_controller)
        self._runtime_single_gauge_var.set(bool(settings.pfeiffer_single_gauge))
        self._runtime_chamber_channel_var.set(str(int(settings.pfeiffer_maxi_chamber_channel)))
        self._runtime_load_channel_var.set(str(int(settings.pfeiffer_maxi_load_channel)))
        self._runtime_settings_path_var.set(self._format_runtime_path(self._runtime_settings_path))

        for key, var in self._runtime_port_vars.items():
            var.set(settings.ports.get(key, ""))

    def _load_default_runtime_settings(self) -> None:
        self._runtime_settings_path = None
        self._sync_runtime_form_from_settings(default_runtime_settings())
        self._log("Runtime-Form auf Default-Konfiguration gesetzt.")

    def _show_available_ports(self) -> None:
        ports = list_serial_ports()
        if not ports:
            self._log("Keine seriellen Ports gefunden (oder pyserial nicht installiert).")
            return
        self._log("Gefundene serielle Ports: " + ", ".join(ports))

    def _runtime_settings_from_form(self) -> RuntimeSettings:
        mode = str(self._runtime_mode_var.get()).strip().lower()
        if mode.startswith("sim"):
            simulation = True
        elif mode.startswith("real"):
            simulation = False
        else:
            raise ValueError("Modus muss 'Simulation' oder 'Real hardware' sein.")

        pfeiffer_controller = str(self._runtime_pfeiffer_var.get()).strip().lower()
        if pfeiffer_controller not in {"maxigauge", "tpg262"}:
            raise ValueError("Pfeiffer-Backend muss 'maxigauge' oder 'tpg262' sein.")

        try:
            chamber_channel = int(str(self._runtime_chamber_channel_var.get()).strip())
            load_channel = int(str(self._runtime_load_channel_var.get()).strip())
        except Exception as exc:
            raise ValueError(f"MaxiGauge-Kanaele ungueltig: {exc}") from exc

        if not (1 <= chamber_channel <= 6):
            raise ValueError("Maxi CH Chamber muss zwischen 1 und 6 liegen.")
        if not (1 <= load_channel <= 6):
            raise ValueError("Maxi CH Load muss zwischen 1 und 6 liegen.")

        ports = {key: str(var.get()).strip() for key, var in self._runtime_port_vars.items()}
        if not simulation:
            missing = [key for key, value in ports.items() if value == ""]
            if missing:
                raise ValueError("Im Real-Mode duerfen keine leeren Ports gesetzt sein: " + ", ".join(missing))

        return RuntimeSettings(
            simulation=simulation,
            ports=ports,
            pfeiffer_controller=pfeiffer_controller,
            pfeiffer_single_gauge=bool(self._runtime_single_gauge_var.get()),
            pfeiffer_maxi_chamber_channel=chamber_channel,
            pfeiffer_maxi_load_channel=load_channel,
        )

    @staticmethod
    def _duplicate_ports(ports: dict[str, str]) -> dict[str, list[str]]:
        reverse: dict[str, list[str]] = {}
        for device, port in ports.items():
            token = str(port).strip()
            if not token:
                continue
            reverse.setdefault(token, []).append(device)
        return {port: devices for port, devices in reverse.items() if len(devices) > 1}

    def _validate_runtime_settings_for_apply(self, settings: RuntimeSettings, *, source: str | None = None) -> bool:
        source_token = str(source or "").strip().lower()
        allow_nanotec_disconnect = source_token == "nanotec"

        if not settings.simulation:
            missing = [key for key, value in settings.ports.items() if str(value).strip() == ""]
            if missing:
                if allow_nanotec_disconnect and missing == ["nanotec"]:
                    confirmed = messagebox.askyesno(
                        "Nanotec-Port trennen",
                        (
                            "Der Nanotec-Port ist leer.\n\n"
                            "Damit wird Nanotec im Realmodus gezielt getrennt.\n"
                            "Controller trotzdem neu starten?"
                        ),
                        parent=self,
                    )
                    if not confirmed:
                        return False
                else:
                    messagebox.showerror(
                        "Runtime-Settings ungueltig",
                        "Im Real-Mode duerfen keine leeren Ports gesetzt sein: " + ", ".join(missing),
                        parent=self,
                    )
                    return False

            duplicates = self._duplicate_ports(settings.ports)
            if duplicates:
                duplicate_text = "; ".join(f"{port} -> {', '.join(sorted(devices))}" for port, devices in duplicates.items())
                confirmed = messagebox.askyesno(
                    "Portzuordnung pruefen",
                    (
                        "Mehrere Geraete teilen denselben Port:\n\n"
                        f"{duplicate_text}\n\n"
                        "Das fuehrt im Realbetrieb oft zu Fehlern. Trotzdem neu starten?"
                    ),
                    parent=self,
                )
                if not confirmed:
                    return False

        return True

    def _apply_runtime_settings(self) -> None:
        try:
            settings = self._runtime_settings_from_form()
        except Exception as exc:
            messagebox.showerror("Runtime-Settings ungueltig", str(exc), parent=self)
            return

        if not self._validate_runtime_settings_for_apply(settings):
            return

        self._restart_controller_with_settings(settings)

    def _restart_controller_with_settings(
        self,
        settings: RuntimeSettings,
        *,
        close_subwindows: bool = True,
        keep_subwindow: str | None = None,
    ) -> None:
        if close_subwindows:
            self._close_subwindows()
        else:
            # Bei Runtime-Wechsel aus einem Unterfenster halten wir genau dieses
            # Fenster offen und aktualisieren spaeter seinen Controller-Handle.
            keep_token = str(keep_subwindow or "pump").strip().lower()
            if keep_token not in {"pump", "pinnacle", "nanotec"}:
                keep_token = "pump"

            if keep_token != "pump" and self._pump_window is not None and self._pump_window.winfo_exists():
                try:
                    self._pump_window.stop_monitoring(close_csv=True, keep_time=False, quiet=True)
                except Exception:
                    pass
                try:
                    self._pump_window.close_window()
                except Exception:
                    pass
                self._pump_window = None

            if keep_token != "pinnacle" and self._pinnacle_window is not None and self._pinnacle_window.winfo_exists():
                try:
                    self._pinnacle_window.close_window()
                except Exception:
                    pass
                self._pinnacle_window = None

            if keep_token != "nanotec" and self._nanotec_window is not None and self._nanotec_window.winfo_exists():
                try:
                    self._nanotec_window.close_window()
                except Exception:
                    pass
                self._nanotec_window = None

        try:
            self.ctrl.shutdown()
        except Exception:
            pass

        self.ctrl = Controller(on_message=self._log, runtime=settings)
        self._schema_pressure_history.clear()
        if self._pump_window is not None and self._pump_window.winfo_exists():
            try:
                self._pump_window.set_controller(self.ctrl)
            except Exception as exc:
                self._log(f"pump window controller refresh error: {exc}")
        if self._pinnacle_window is not None and self._pinnacle_window.winfo_exists():
            try:
                self._pinnacle_window.set_controller(self.ctrl)
            except Exception as exc:
                self._log(f"pinnacle window controller refresh error: {exc}")
        if self._nanotec_window is not None and self._nanotec_window.winfo_exists():
            try:
                self._nanotec_window.set_controller(self.ctrl)
            except Exception as exc:
                self._log(f"nanotec window controller refresh error: {exc}")
        self._sync_runtime_form_from_settings(settings)
        self._load_all_quick_controls_from_state()
        self._update_schema_view(self.ctrl.state)
        self._log(
            "Controller neu gestartet: "
            + ("Simulation" if settings.simulation else "Real hardware")
            + f", Pfeiffer={settings.pfeiffer_controller}"
        )

    def _load_runtime_settings_from_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Runtime-Settings laden",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return

        try:
            loaded = load_runtime_settings(selected, base=default_runtime_settings())
        except Exception as exc:
            messagebox.showerror("Settings-Datei ungueltig", str(exc), parent=self)
            return

        self._runtime_settings_path = Path(selected).expanduser().resolve()
        self._sync_runtime_form_from_settings(loaded)
        self._restart_controller_with_settings(loaded)
        self._log(f"Runtime-Settings aus Datei geladen: {self._runtime_settings_path}")

    def _save_runtime_settings_to_file(self) -> None:
        try:
            settings = self._runtime_settings_from_form()
        except Exception as exc:
            messagebox.showerror("Runtime-Settings ungueltig", str(exc), parent=self)
            return

        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Runtime-Settings speichern",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="sputter_settings.json",
        )
        if not selected:
            return

        try:
            saved = save_runtime_settings(selected, settings)
        except Exception as exc:
            messagebox.showerror("Settings konnten nicht gespeichert werden", str(exc), parent=self)
            return

        self._runtime_settings_path = saved
        self._runtime_settings_path_var.set(self._format_runtime_path(saved))
        self._log(f"Runtime-Settings gespeichert: {saved}")

    # ------------------------------------------------------------------
    # Schnellsteuerung: apply/load
    # ------------------------------------------------------------------
    def _load_all_quick_controls_from_state(self) -> None:
        self._load_pinnacle_quick_controls_from_state()
        self._load_motor_quick_controls_from_state(1)
        self._load_motor_quick_controls_from_state(2)
        self._load_fug_quick_controls_from_state()
        self._argon_set_var.set(f"{float(self.ctrl.state.expert.argon_set):.3f}")

    def _load_pinnacle_quick_controls_from_state(self) -> None:
        mapping = {"A": self.ctrl.state.pin_a, "B": self.ctrl.state.pin_b}
        for channel, state_channel in mapping.items():
            ui = self._pin_controls.get(channel)
            if ui is None:
                continue
            ui["mode_var"].set(state_channel.mode.value)
            ui["setpoint_var"].set(f"{float(state_channel.setpoint):.3f}")
            ui["freq_var"].set(str(int(state_channel.pulse_frequency_index) * 5))
            ui["reverse_var"].set(f"{int(state_channel.pulse_reverse_index) * 0.1:.1f}")

    def _load_motor_quick_controls_from_state(self, motor_index: int) -> None:
        state = self.ctrl.state.motor1 if motor_index == 1 else self.ctrl.state.motor2
        ui = self._motor_controls.get(motor_index)
        if ui is None:
            return
        ui["speed_var"].set(str(int(state.target_speed)))
        ui["pos_var"].set(f"{float(state.target_position_mm):.3f}")
        ui["step_var"].set(str(int(state.step_mode_to_set)))
        ui["dir_var"].set(state.direction.value)
        ui["ref_var"].set(state.reference_direction.value)
        ui["loops_var"].set(str(int(state.loops)))

    def _load_fug_quick_controls_from_state(self) -> None:
        fug = self.ctrl.state.fug
        self._fug_voltage_set_var.set(f"{float(fug.voltage_set):.3f}")
        self._fug_current_set_var.set(f"{float(fug.current_set):.6f}")
        self._fug_voltage_ramp_var.set(f"{float(fug.voltage_ramp):.3f}")
        self._fug_current_ramp_var.set(f"{float(fug.current_ramp):.6f}")

    def _apply_argon_setpoint(self) -> None:
        try:
            value = float(self._argon_set_var.get().strip())
        except Exception as exc:
            messagebox.showerror("Argon Setpoint ungueltig", str(exc), parent=self)
            return
        self.ctrl.set_argon_setpoint(value)
        self._log(f"Argon setpoint gesetzt: {value:.3f}")

    def _apply_pinnacle_quick(self, channel: str) -> None:
        ui = self._pin_controls.get(channel)
        if ui is None:
            return
        try:
            mode_text = str(ui["mode_var"].get())
            setpoint = float(str(ui["setpoint_var"].get()).strip())
            freq = float(str(ui["freq_var"].get()).strip())
            reverse = float(str(ui["reverse_var"].get()).strip())
        except Exception as exc:
            messagebox.showerror(f"Pinnacle Kanal {channel}: Eingabe ungueltig", str(exc), parent=self)
            return

        self.ctrl.set_pinnacle_channel_mode(channel, mode_text)
        self.ctrl.set_pinnacle_channel_setpoint(channel, setpoint)
        self.ctrl.set_pinnacle_channel_pulse_frequency_khz(channel, freq)
        self.ctrl.set_pinnacle_channel_pulse_reverse_us(channel, reverse)
        self._log(
            f"Pinnacle Kanal {channel}: Sollwerte aktualisiert "
            f"(mode={mode_text}, setpoint={setpoint}, freq={freq}kHz, reverse={reverse}us)"
        )

    def _apply_motor_quick(self, motor_index: int) -> None:
        ui = self._motor_controls.get(motor_index)
        if ui is None:
            return

        try:
            ok = self.ctrl.configure_motor(
                motor_index,
                target_speed=float(str(ui["speed_var"].get()).strip()),
                target_position_mm=float(str(ui["pos_var"].get()).strip()),
                step_mode=int(str(ui["step_var"].get()).strip()),
                direction=str(ui["dir_var"].get()).strip(),
                reference_direction=str(ui["ref_var"].get()).strip(),
                loops=int(float(str(ui["loops_var"].get()).strip())),
            )
        except Exception as exc:
            messagebox.showerror(f"Motor {motor_index}: Eingabe ungueltig", str(exc), parent=self)
            return

        if ok:
            self._log(f"Motor {motor_index}: Sollwerte angewendet.")
        else:
            self._log(f"Motor {motor_index}: Sollwerte konnten nicht angewendet werden.")

    def _apply_fug_quick(self) -> None:
        try:
            v_set = float(self._fug_voltage_set_var.get().strip())
            i_set = float(self._fug_current_set_var.get().strip())
            v_ramp = float(self._fug_voltage_ramp_var.get().strip())
            i_ramp = float(self._fug_current_ramp_var.get().strip())
        except Exception as exc:
            messagebox.showerror("FUG Eingabe ungueltig", str(exc), parent=self)
            return

        self.ctrl.set_fug_voltage_setpoint(v_set)
        self.ctrl.set_fug_current_setpoint(i_set)
        self.ctrl.set_fug_voltage_ramp(v_ramp)
        self.ctrl.set_fug_current_ramp(i_ramp)
        self._log(
            f"FUG Sollwerte aktualisiert (V={v_set:.2f}, I={i_set:.4f}, "
            f"V-ramp={v_ramp:.2f}, I-ramp={i_ramp:.4f})"
        )

    def _set_pinnacle_output_from_main(self, channel: str, on: bool) -> None:
        """
        Sicherheitswrapper fuer Pinnacle-ON/OFF aus der Hauptseite.

        Warum dieser Wrapper?
        - Die Hauptseite ist eine Schnellbedienung.
        - Fuer OUTPUT EIN wollen wir trotzdem eine kurze Bestaetigung erzwingen,
          damit versehentliche Klicks bei Hochspannung minimiert werden.
        """

        if on:
            confirmed = messagebox.askyesno(
                "Pinnacle Output EIN bestaetigen",
                (
                    f"Kanal {channel} auf OUTPUT EIN setzen?\n\n"
                    "Bitte nur bestaetigen, wenn Vakuum, Interlocks und "
                    "Verdrahtung geprueft sind."
                ),
                parent=self,
            )
            if not confirmed:
                return

        self.ctrl.set_pinnacle_channel_active(channel, on)
        self._log(f"Pinnacle Kanal {channel}: Output-Sollzustand -> {'EIN' if on else 'AUS'}")

    def _start_motor_from_main(self, motor_index: int) -> None:
        """
        Sicherheitswrapper fuer Motorstart aus der Hauptseite.
        """

        confirmed = messagebox.askyesno(
            "Motorstart bestaetigen",
            (
                f"Motor {motor_index} starten?\n\n"
                "Bitte nur bestaetigen, wenn Fahrweg frei ist und keine "
                "Kollision moeglich ist."
            ),
            parent=self,
        )
        if not confirmed:
            return

        self.ctrl.start_motor(motor_index)

    def _reference_motor_from_main(self, motor_index: int) -> None:
        """
        Sicherheitswrapper fuer Referenzfahrt aus der Hauptseite.
        """

        confirmed = messagebox.askyesno(
            "Referenzfahrt bestaetigen",
            (
                f"Motor {motor_index}: Referenzfahrt starten?\n\n"
                "Bitte nur bestaetigen, wenn der Fahrweg frei ist."
            ),
            parent=self,
        )
        if not confirmed:
            return

        self.ctrl.reference_motor(motor_index)

    # ------------------------------------------------------------------
    # Reconnect-Klickpfade
    # ------------------------------------------------------------------
    def _reconnect_gauges(self) -> None:
        self._log("Reconnect Gauges angefordert")
        self.ctrl.reconnect_pfeiffer()

    def _reconnect_pinnacle(self) -> None:
        self._log("Reconnect Pinnacle angefordert")
        self.ctrl.reconnect_pinnacle()

    def _reconnect_nanotec(self) -> None:
        self._log("Reconnect Nanotec angefordert")
        self.ctrl.reconnect_nanotec()

    def _reconnect_fug(self) -> None:
        self._log("Reconnect FUG angefordert")
        self.ctrl.reconnect_fug()

    def _reconnect_expert(self) -> None:
        self._log("Reconnect Expert angefordert")
        self.ctrl.reconnect_expert()

    # ------------------------------------------------------------------
    # Tick + Anzeigeupdate
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        """
        Zentraler GUI-Takt:
        1) Controller aktualisieren.
        2) Gesamtuebersicht aktualisieren.
        3) Offene Unterfenster synchronisieren.
        4) Naechsten Tick planen.
        """

        try:
            self.ctrl.tick()
            state = self.ctrl.state

            runtime = self.ctrl.get_runtime_settings()
            if runtime.pfeiffer_controller == "maxigauge":
                backend = f"maxigauge CH{runtime.pfeiffer_maxi_chamber_channel}/CH{runtime.pfeiffer_maxi_load_channel}"
            else:
                backend = "tpg262 single" if runtime.pfeiffer_single_gauge else "tpg262 dual"
            self._mode_var.set(
                f"{'Simulation' if state.simulation else 'Real hardware'} | "
                f"Pfeiffer: {backend} | Tick={TIMER_INTERVAL_SEC:.3f}s"
            )

            self._update_port_texts(state)
            self._update_vacuum_texts(state)
            self._update_pinnacle_texts(state)
            self._update_motor_texts(state)
            self._update_fug_texts(state)
            self._update_schema_view(state)

            # Unterfenster robust aktualisieren.
            if self._pump_window is not None and self._pump_window.winfo_exists():
                try:
                    self._pump_window.on_state_tick(state)
                except Exception as exc:
                    self._log(f"pump window update error: {exc}")
            if self._pinnacle_window is not None and self._pinnacle_window.winfo_exists():
                try:
                    self._pinnacle_window.on_state_tick(state)
                except Exception as exc:
                    self._log(f"pinnacle window update error: {exc}")
            if self._nanotec_window is not None and self._nanotec_window.winfo_exists():
                try:
                    self._nanotec_window.on_state_tick(state)
                except Exception as exc:
                    self._log(f"nanotec window update error: {exc}")
        except Exception as exc:
            self._log(f"[ERR] Main tick failed: {exc}")
        finally:
            try:
                self.after(int(TIMER_INTERVAL_SEC * 1000), self._tick)
            except Exception:
                pass

    def _update_port_texts(self, state: PlantState) -> None:
        for key, var in self._port_vars.items():
            runtime = state.ports.get(key)
            if runtime is None:
                var.set(f"{key}: n/a")
                continue
            err = runtime.last_error.strip()
            err_text = "ok" if not err else err
            var.set(
                f"{key}: connected={runtime.connected}, failed={runtime.failed}, ready={runtime.ready}, error={err_text}"
            )

    def _update_vacuum_texts(self, state: PlantState) -> None:
        runtime = self.ctrl.get_runtime_settings()
        if runtime.pfeiffer_controller == "maxigauge":
            gauge_backend = (
                f"maxigauge CH{runtime.pfeiffer_maxi_chamber_channel}/"
                f"CH{runtime.pfeiffer_maxi_load_channel}"
            )
        else:
            gauge_backend = "tpg262" + (" single" if runtime.pfeiffer_single_gauge else " dual")

        self._vacuum_text_var.set(
            "Gauge backend={} | P chamber={:.3e} mbar (status {}), P load={:.3e} mbar (status {}), chamber_sensor_on={}, load_sensor_on={}".format(
                gauge_backend,
                state.vacuum.p_chamber,
                int(state.vacuum.p_chamber_status),
                state.vacuum.p_load,
                int(state.vacuum.p_load_status),
                state.vacuum.chamber_sensor_on,
                state.vacuum.load_sensor_on,
            )
        )
        self._argon_text_var.set(
            "Argon: valve_open={}, set={:.3f}, actual={:.3f}".format(
                state.valves.ar_valve_open,
                float(state.expert.argon_set),
                float(state.expert.argon_actual),
            )
        )
        self._valve_text_var.set(
            "Valves: bypass_load={}, vat_load={}, back_load={}, gate={}, bypass_chamber={}, back_chamber={}, vat_chamber_mode={}".format(
                state.valves.bypass_load_open,
                state.valves.vat_load_open,
                state.valves.back_valve_load_open,
                state.valves.gate_load_open,
                state.valves.bypass_chamber_open,
                state.valves.back_valve_chamber_open,
                state.valves.vat_chamber,
            )
        )

    def _update_pinnacle_texts(self, state: PlantState) -> None:
        for channel, channel_state in (("A", state.pin_a), ("B", state.pin_b)):
            var = self._pin_live_vars.get(channel)
            if var is None:
                continue
            var.set(
                "Output={} | Mode={} | Set(Ist)={:.3f} | U={:.1f}V I={:.3f}A P={:.3f}W | f={}kHz rev={:.1f}us".format(
                    "ON" if channel_state.active else "OFF",
                    channel_state.regulation,
                    float(channel_state.setpoint_actual),
                    float(channel_state.voltage),
                    float(channel_state.current),
                    float(channel_state.power),
                    int(channel_state.act_pulse_frequency),
                    float(channel_state.act_pulse_reverse_time),
                )
            )

    def _update_motor_texts(self, state: PlantState) -> None:
        for motor_index, motor in ((1, state.motor1), (2, state.motor2)):
            var = self._motor_live_vars.get(motor_index)
            if var is None:
                continue
            var.set(
                "connected={} running={} status={}({}) pos={:.3f}mm enc={:.3f}mm progress={:.1f}% limits[L={},R={}] stop_reason={}".format(
                    motor.connected,
                    motor.running,
                    int(motor.status_code),
                    motor.status_text,
                    float(motor.actual_position_mm),
                    float(motor.encoder_position_mm),
                    float(motor.progress_percent),
                    motor.limit_left_active,
                    motor.limit_right_active,
                    motor.limit_stop_reason if motor.limit_stop_reason else "-",
                )
            )

    def _update_fug_texts(self, state: PlantState) -> None:
        self._fug_state_var.set(
            "HV={} | U_actual={:.1f}V I_actual={:.4f}A".format(
                state.fug.hv_on,
                float(state.fug.voltage_actual),
                float(state.fug.current_actual),
            )
        )

    # ------------------------------------------------------------------
    # Unterfenster
    # ------------------------------------------------------------------
    def _open_pump_window(self) -> None:
        if self._pump_window is not None and self._pump_window.winfo_exists():
            self._pump_window.lift()
            self._pump_window.focus_force()
            return
        self._pump_window = VacuumPumpWindow(
            self,
            self.ctrl,
            get_runtime_settings=self._runtime_settings_for_child,
            apply_runtime_settings=self._apply_runtime_settings_from_child,
            list_serial_ports_cb=self._list_ports_for_child,
        )
        try:
            self._pump_window.on_state_tick(self.ctrl.state)
        except Exception as exc:
            self._log(f"pump window initial update error: {exc}")

    def _runtime_settings_for_child(self) -> RuntimeSettings:
        return self.ctrl.get_runtime_settings()

    def _apply_runtime_settings_from_child(self, settings: RuntimeSettings, *, source: str = "pump") -> None:
        if not self._validate_runtime_settings_for_apply(settings, source=source):
            return
        self._restart_controller_with_settings(settings, close_subwindows=False, keep_subwindow=source)

    @staticmethod
    def _list_ports_for_child() -> list[str]:
        return list_serial_ports()

    def _open_pinnacle_window(self) -> None:
        if self._pinnacle_window is not None and self._pinnacle_window.winfo_exists():
            self._pinnacle_window.lift()
            self._pinnacle_window.focus_force()
            return
        self._pinnacle_window = PinnacleWindow(
            self,
            self.ctrl,
            get_runtime_settings=self._runtime_settings_for_child,
            apply_runtime_settings=lambda settings: self._apply_runtime_settings_from_child(settings, source="pinnacle"),
            list_serial_ports_cb=self._list_ports_for_child,
        )
        try:
            self._pinnacle_window.on_state_tick(self.ctrl.state)
        except Exception as exc:
            self._log(f"pinnacle window initial update error: {exc}")

    def _open_nanotec_window(self) -> None:
        if self._nanotec_window is not None and self._nanotec_window.winfo_exists():
            self._nanotec_window.lift()
            self._nanotec_window.focus_force()
            return
        self._nanotec_window = NanotecWindow(
            self,
            self.ctrl,
            get_runtime_settings=self._runtime_settings_for_child,
            apply_runtime_settings=lambda settings: self._apply_runtime_settings_from_child(settings, source="nanotec"),
            list_serial_ports_cb=self._list_ports_for_child,
        )
        try:
            self._nanotec_window.on_state_tick(self.ctrl.state)
        except Exception as exc:
            self._log(f"nanotec window initial update error: {exc}")

    # ------------------------------------------------------------------
    # Logging / Lifecycle
    # ------------------------------------------------------------------
    def _argon_toggle(self) -> None:
        self.ctrl.toggle_argon()

    def _log(self, text: str) -> None:
        if self.logbox is None:
            self._early_log_messages.append(text)
            return
        self.logbox.insert("end", text)
        self.logbox.yview_moveto(1.0)

    def _flush_early_logs(self) -> None:
        if self.logbox is None:
            return
        if not self._early_log_messages:
            return
        for msg in self._early_log_messages:
            self.logbox.insert("end", msg)
        self._early_log_messages.clear()
        self.logbox.yview_moveto(1.0)

    def _close_subwindows(self) -> None:
        if self._pump_window is not None and self._pump_window.winfo_exists():
            try:
                self._pump_window.close_window()
            except Exception:
                pass
        self._pump_window = None

        if self._pinnacle_window is not None and self._pinnacle_window.winfo_exists():
            try:
                self._pinnacle_window.close_window()
            except Exception:
                pass
        self._pinnacle_window = None

        if self._nanotec_window is not None and self._nanotec_window.winfo_exists():
            try:
                self._nanotec_window.close_window()
            except Exception:
                pass
        self._nanotec_window = None

    def _on_close(self) -> None:
        self._close_subwindows()
        self.ctrl.shutdown()
        self.destroy()


__all__ = ["App"]
