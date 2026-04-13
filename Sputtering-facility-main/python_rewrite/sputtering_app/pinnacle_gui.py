from __future__ import annotations

"""
Spezielle Unter-GUI fuer den Pinnacle-MDX-Bereich.

Ziel dieses Fensters:
- Eine klar strukturierte, anfaengerfreundliche Bedienoberflaeche fuer
  die beiden Pinnacle-Kanaele (A/B) bereitstellen.
- Funktional an den C++-Originaldialog angelehnt bleiben:
  - Kanal A + Kanal B mit jeweils Sollwerten und Istwerten
  - Regelmodus, Setpoint, Pulsfrequenz, Puls-Umkehrzeit
  - ON/OFF-Bedienung
  - Spannungsplot

Sicherheitsprinzipien in dieser GUI:
1) Kritische Aktionen (Output EIN) werden explizit bestaetigt.
2) Es gibt einen leicht erreichbaren "Not-Aus aller Kanaele"-Button.
3) Die GUI oeffnet keine eigenen seriellen Ports, sondern nutzt den bereits
   vorhandenen zentralen Controller-State.
"""

import time
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Callable

import tkinter as tk
from tkinter import messagebox, ttk

from .models import PinnacleChannelState, PlantState, RegulationMode

if TYPE_CHECKING:
    from .controller import Controller
    from .runtime_settings import RuntimeSettings


class PinnacleWindow(tk.Toplevel):
    """
    Eigenstaendiges Fenster fuer die Pinnacle-MDX-Bedienung.

    Wichtige Designentscheidung:
    - Dieses Fenster arbeitet ausschliesslich mit dem zentralen Controller.
    - Dadurch gibt es nur eine Quelle fuer den Anlagenzustand und keine
      konkurrierenden seriellen Zugriffe.
    """

    def __init__(
        self,
        parent: tk.Misc,
        controller: "Controller",
        *,
        get_runtime_settings: Callable[[], "RuntimeSettings"] | None = None,
        apply_runtime_settings: Callable[["RuntimeSettings"], None] | None = None,
        list_serial_ports_cb: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._get_runtime_settings = get_runtime_settings
        self._apply_runtime_settings = apply_runtime_settings
        self._list_serial_ports_cb = list_serial_ports_cb

        # Fenstergrunddaten.
        self.title("Pinnacle MDX (Bedienung + Plot)")
        self.geometry("1320x860")
        self.minsize(1100, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Laufzeitdaten fuer Plot und Samples.
        self._sample_counter = 0
        self._plot_t0_monotonic: float | None = None
        self._plot_times_s: list[float] = []
        self._plot_voltage_a: list[float] = []
        self._plot_voltage_b: list[float] = []

        # Plotobjekte (nur gesetzt, wenn matplotlib verfuegbar ist).
        self._plot_available = False
        self._plot_modules = None
        self._figure = None
        self._ax_a = None
        self._ax_b = None
        self._line_a = None
        self._line_b = None
        self._canvas = None

        # Kopfzeilen-/Statusvariablen.
        runtime = self._runtime_settings_snapshot()
        self._mode_var = tk.StringVar(value="Simulation" if runtime.simulation else "Real hardware")
        self._mode_select_var = tk.StringVar(value="simulation" if runtime.simulation else "real")
        self._mode_badge_var = tk.StringVar(value=self._mode_badge_text(runtime.simulation))
        self._backend_health_var = tk.StringVar(value="pinnacle backend: unknown")
        self._sample_count_var = tk.StringVar(value="0")
        self._last_update_age_var = tk.StringVar(value="Age: -")
        self._port_conflict_var = tk.StringVar(value="")

        serial_cfg = self._controller.get_pinnacle_serial_settings()
        tuning_cfg = self._controller.get_pinnacle_runtime_options()
        self._serial_port_var = tk.StringVar(value=str(serial_cfg["port"]))
        self._serial_baud_var = tk.StringVar(value=str(int(serial_cfg["baudrate"])))
        self._serial_parity_var = tk.StringVar(value=str(serial_cfg["parity"]).upper())
        self._serial_timeout_var = tk.StringVar(value=f"{float(serial_cfg['timeout']):.3f}")
        self._serial_bytesize_var = tk.StringVar(value=str(int(serial_cfg["bytesize"])))
        self._serial_stopbits_var = tk.StringVar(value=str(int(serial_cfg["stopbits"])))

        self._addr_a_var = tk.StringVar(value=str(int(self._controller.state.pin_a.address)))
        self._addr_b_var = tk.StringVar(value=str(int(self._controller.state.pin_b.address)))

        self._strict_protocol_var = tk.BooleanVar(value=bool(tuning_cfg["strict_protocol"]))
        self._verify_apply_var = tk.BooleanVar(value=bool(tuning_cfg["verify_after_apply"]))
        self._fast_emergency_off_var = tk.BooleanVar(value=bool(tuning_cfg["fast_emergency_off"]))
        self._query_retries_var = tk.StringVar(value=str(int(tuning_cfg["query_retries"])))
        self._command_delay_ms_var = tk.StringVar(value=f"{float(tuning_cfg['command_delay_s']) * 1000.0:.0f}")
        self._response_read_size_var = tk.StringVar(value=str(int(tuning_cfg["response_read_size"])))
        self._confirm_on_var = tk.BooleanVar(value=True)

        # Erweiterte Plotoptionen, angelehnt an die C++-Plotseite:
        # - Y-Log global ein/aus
        # - Minor-Grid global ein/aus
        # - optional fixe Y-Grenzen pro Kanal
        # - optionales Zeitfenster in Sekunden
        self._plot_y_log_var = tk.BooleanVar(value=False)
        self._plot_minor_grid_var = tk.BooleanVar(value=True)
        self._plot_keep_a_limits_var = tk.BooleanVar(value=False)
        self._plot_keep_b_limits_var = tk.BooleanVar(value=False)
        self._plot_a_ymin_var = tk.StringVar(value="0")
        self._plot_a_ymax_var = tk.StringVar(value="1300")
        self._plot_b_ymin_var = tk.StringVar(value="0")
        self._plot_b_ymax_var = tk.StringVar(value="1300")
        self._plot_window_s_var = tk.StringVar(value="0")

        # Interne, validierte Konfiguration (wird nur in
        # `_apply_plot_settings()` gesetzt).
        self._plot_use_log = False
        self._plot_use_minor_grid = True
        self._plot_keep_a_limits = False
        self._plot_keep_b_limits = False
        self._plot_a_ymin = 0.0
        self._plot_a_ymax = 1300.0
        self._plot_b_ymin = 0.0
        self._plot_b_ymax = 1300.0
        self._plot_window_seconds = 0.0

        # Kanalgebundene GUI-Variablen.
        # Wir halten alles pro Kanal in einem Dictionary, damit A und B mit
        # identischer Logik verarbeitet werden koennen.
        self._channel_ui: dict[str, dict[str, object]] = {}

        self._build_ui()
        self._update_mode_button_style()
        self._refresh_serial_ports()
        self._update_port_conflict_info()
        self._load_control_values_from_state()
        self._log("Pinnacle-Fenster geoeffnet. Bedienung erfolgt ueber den zentralen Controller.")

    # ------------------------------------------------------------------
    # Runtime bridge
    # ------------------------------------------------------------------
    def _runtime_settings_snapshot(self) -> "RuntimeSettings":
        if self._get_runtime_settings is not None:
            return self._get_runtime_settings()
        return self._controller.get_runtime_settings()

    @staticmethod
    def _mode_badge_text(simulation: bool) -> str:
        return "AKTIV: SIMULATION" if simulation else "AKTIV: REAL HARDWARE"

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """
        Baut die komplette Fensterstruktur.

        Struktur:
        - Kopfbereich mit klarem Runtime-Mode, Status und globalen Aktionen
        - Notebook mit 2 Seiten:
          1) Bedienung (Kanal A/B)
          2) Spannungsplot (A/B)
        - Meldungsbereich
        """

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        # ---------------- Kopfbereich ----------------
        top = ttk.Frame(outer)
        top.pack(fill="x")
        top.columnconfigure(0, weight=0)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, weight=1)

        mode_card = ttk.LabelFrame(top, text="Betriebsmodus", padding=10)
        mode_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        mode_card.columnconfigure(1, weight=1)

        self._mode_badge_label = tk.Label(
            mode_card,
            textvariable=self._mode_badge_var,
            font=("TkDefaultFont", 11, "bold"),
            fg="white",
            bg="#1f6f43",
            padx=10,
            pady=6,
        )
        self._mode_badge_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(mode_card, text="Auswahl:").grid(row=1, column=0, sticky="w")
        mode_cb = ttk.Combobox(
            mode_card,
            textvariable=self._mode_select_var,
            values=["simulation", "real"],
            state="readonly",
            width=12,
        )
        mode_cb.grid(row=1, column=1, sticky="w", padx=(4, 10))
        mode_cb.bind("<<ComboboxSelected>>", lambda _e: self._update_mode_button_style())

        self._mode_apply_button = tk.Button(
            mode_card,
            text="SIM anwenden",
            command=self._apply_mode,
            bg="#dbeafe",
            fg="#0b3b66",
            activebackground="#bfdbfe",
            relief="raised",
            bd=1,
            padx=10,
            pady=4,
        )
        self._mode_apply_button.grid(row=1, column=2, sticky="ew")

        ttk.Label(mode_card, text="Aktuell:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(mode_card, textvariable=self._mode_var, font=("TkDefaultFont", 10, "bold")).grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        status_card = ttk.LabelFrame(top, text="Pinnacle-Status", padding=10)
        status_card.grid(row=0, column=1, sticky="nsew", padx=4)
        status_card.columnconfigure(1, weight=1)

        ttk.Label(status_card, text="Backend:").grid(row=0, column=0, sticky="nw", padx=(0, 6), pady=2)
        ttk.Label(status_card, textvariable=self._backend_health_var, wraplength=560, justify="left").grid(
            row=0,
            column=1,
            sticky="w",
            pady=2,
        )
        ttk.Label(status_card, text="Samples:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        ttk.Label(status_card, textvariable=self._sample_count_var).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(status_card, textvariable=self._last_update_age_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        action_card = ttk.LabelFrame(top, text="Globale Aktionen", padding=10)
        action_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        action_card.columnconfigure(0, weight=1)

        tk.Button(
            action_card,
            text="NOT-AUS Pinnacle (A+B OFF)",
            command=self._emergency_off_all,
            bg="#7f1d1d",
            fg="white",
            activebackground="#991b1b",
            activeforeground="white",
            relief="raised",
            bd=1,
            padx=8,
            pady=5,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))

        ttk.Button(
            action_card,
            text="Sollwerte aus State laden",
            command=self._load_control_values_from_state,
        ).grid(row=1, column=0, sticky="ew", pady=3)

        ttk.Button(
            action_card,
            text="Pinnacle-Backend neu verbinden",
            command=self._reconnect_pinnacle_backend,
        ).grid(row=2, column=0, sticky="ew", pady=3)

        # ---------------- Hauptinhalt ----------------
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=(10, 0))

        tab_control = ttk.Frame(notebook, padding=8)
        notebook.add(tab_control, text="Bedienung")

        tab_plot = ttk.Frame(notebook, padding=8)
        notebook.add(tab_plot, text="Spannungsplot")

        # Bedienseite: zwei gleichartige Kanalframes nebeneinander.
        tab_control.columnconfigure(0, weight=1)
        tab_control.columnconfigure(1, weight=1)
        tab_control.rowconfigure(1, weight=1)

        service = ttk.LabelFrame(tab_control, text="Ansteuerung / Verbindung (flexibel)", padding=8)
        service.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        service.columnconfigure(1, weight=1)
        service.columnconfigure(3, weight=1)
        service.columnconfigure(5, weight=1)
        service.columnconfigure(7, weight=1)

        ttk.Label(service, text="Pinnacle Port:").grid(row=0, column=0, sticky="w")
        self._port_combo = ttk.Combobox(service, textvariable=self._serial_port_var, width=18)
        self._port_combo.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Button(service, text="Ports scannen", command=self._refresh_serial_ports).grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(service, text="Port + Modus anwenden", command=self._apply_port_and_mode).grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Button(service, text="Adresse A/B anwenden", command=self._apply_channel_addresses).grid(row=0, column=4, sticky="ew", padx=4)
        ttk.Button(service, text="A/B Ping", command=self._ping_channels).grid(row=0, column=5, sticky="ew", padx=4)
        ttk.Label(service, textvariable=self._port_conflict_var, foreground="#9f1239").grid(
            row=0,
            column=6,
            columnspan=2,
            sticky="w",
            padx=(8, 0),
        )

        ttk.Label(service, text="A-Adresse:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(service, textvariable=self._addr_a_var, width=8).grid(row=1, column=1, sticky="w", padx=(4, 8), pady=(6, 0))
        ttk.Label(service, text="B-Adresse:").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(service, textvariable=self._addr_b_var, width=8).grid(row=1, column=3, sticky="w", padx=(4, 8), pady=(6, 0))

        serial_row = ttk.Frame(service)
        serial_row.grid(row=1, column=4, columnspan=4, sticky="ew", pady=(6, 0))
        serial_row.columnconfigure(9, weight=1)
        ttk.Label(serial_row, text="baud").grid(row=0, column=0, sticky="e")
        ttk.Entry(serial_row, textvariable=self._serial_baud_var, width=7).grid(row=0, column=1, sticky="w", padx=(3, 8))
        ttk.Label(serial_row, text="parity").grid(row=0, column=2, sticky="e")
        ttk.Combobox(serial_row, textvariable=self._serial_parity_var, values=["N", "E", "O"], width=4, state="readonly").grid(
            row=0,
            column=3,
            sticky="w",
            padx=(3, 8),
        )
        ttk.Label(serial_row, text="timeout[s]").grid(row=0, column=4, sticky="e")
        ttk.Entry(serial_row, textvariable=self._serial_timeout_var, width=7).grid(row=0, column=5, sticky="w", padx=(3, 8))
        ttk.Label(serial_row, text="bytesize").grid(row=0, column=6, sticky="e")
        ttk.Entry(serial_row, textvariable=self._serial_bytesize_var, width=4).grid(row=0, column=7, sticky="w", padx=(3, 8))
        ttk.Label(serial_row, text="stopbits").grid(row=0, column=8, sticky="e")
        ttk.Entry(serial_row, textvariable=self._serial_stopbits_var, width=4).grid(row=0, column=9, sticky="w", padx=(3, 8))
        ttk.Button(serial_row, text="Serial live anwenden", command=self._apply_serial_live).grid(row=0, column=10, sticky="e")

        compat = ttk.Frame(service)
        compat.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        compat.columnconfigure(10, weight=1)
        ttk.Checkbutton(compat, text="Strict protocol", variable=self._strict_protocol_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(compat, text="Write verify", variable=self._verify_apply_var).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Checkbutton(compat, text="Fast emergency-off", variable=self._fast_emergency_off_var).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Checkbutton(compat, text="ON mit Bestaetigung", variable=self._confirm_on_var).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(compat, text="retries").grid(row=0, column=4, sticky="e")
        ttk.Entry(compat, textvariable=self._query_retries_var, width=4).grid(row=0, column=5, sticky="w", padx=(3, 8))
        ttk.Label(compat, text="delay[ms]").grid(row=0, column=6, sticky="e")
        ttk.Entry(compat, textvariable=self._command_delay_ms_var, width=6).grid(row=0, column=7, sticky="w", padx=(3, 8))
        ttk.Label(compat, text="read_size").grid(row=0, column=8, sticky="e")
        ttk.Entry(compat, textvariable=self._response_read_size_var, width=6).grid(row=0, column=9, sticky="w", padx=(3, 8))
        ttk.Button(compat, text="Kompatibilitaet anwenden", command=self._apply_runtime_tuning).grid(row=0, column=10, sticky="e")

        frame_a = self._create_channel_panel(tab_control, channel_name="A")
        frame_a.grid(row=1, column=0, sticky="nsew", padx=(0, 6))

        frame_b = self._create_channel_panel(tab_control, channel_name="B")
        frame_b.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        # Plotseite: zuerst Optionen, dann eigentlicher Plot.
        plot_options = ttk.LabelFrame(tab_plot, text="Plot-Optionen", padding=8)
        plot_options.pack(fill="x")
        plot_options.columnconfigure(10, weight=1)

        ttk.Checkbutton(plot_options, text="Y-Log", variable=self._plot_y_log_var).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Checkbutton(plot_options, text="Minor-Grid", variable=self._plot_minor_grid_var).grid(row=0, column=1, sticky="w", padx=(0, 8))

        ttk.Checkbutton(plot_options, text="Fixe Y A", variable=self._plot_keep_a_limits_var).grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Label(plot_options, text="A min:").grid(row=0, column=3, sticky="e")
        ttk.Entry(plot_options, textvariable=self._plot_a_ymin_var, width=8).grid(row=0, column=4, sticky="w", padx=(4, 8))
        ttk.Label(plot_options, text="A max:").grid(row=0, column=5, sticky="e")
        ttk.Entry(plot_options, textvariable=self._plot_a_ymax_var, width=8).grid(row=0, column=6, sticky="w", padx=(4, 8))

        ttk.Checkbutton(plot_options, text="Fixe Y B", variable=self._plot_keep_b_limits_var).grid(row=1, column=2, sticky="w", padx=(0, 8))
        ttk.Label(plot_options, text="B min:").grid(row=1, column=3, sticky="e")
        ttk.Entry(plot_options, textvariable=self._plot_b_ymin_var, width=8).grid(row=1, column=4, sticky="w", padx=(4, 8))
        ttk.Label(plot_options, text="B max:").grid(row=1, column=5, sticky="e")
        ttk.Entry(plot_options, textvariable=self._plot_b_ymax_var, width=8).grid(row=1, column=6, sticky="w", padx=(4, 8))

        ttk.Label(plot_options, text="Zeitfenster [s]:").grid(row=0, column=7, sticky="e")
        ttk.Entry(plot_options, textvariable=self._plot_window_s_var, width=8).grid(row=0, column=8, sticky="w", padx=(4, 8))
        ttk.Button(plot_options, text="Optionen anwenden", command=self._apply_plot_settings).grid(
            row=0,
            column=9,
            rowspan=2,
            sticky="e",
            padx=(4, 0),
        )

        self._plot_container = ttk.Frame(tab_plot)
        self._plot_container.pack(fill="both", expand=True, pady=(8, 0))
        self._initialize_plot_backend()

        # ---------------- Meldungsbereich ----------------
        msg_frame = ttk.LabelFrame(outer, text="Meldungen", padding=6)
        msg_frame.pack(fill="both", expand=False, pady=(10, 0))
        msg_frame.rowconfigure(0, weight=1)
        msg_frame.columnconfigure(0, weight=1)

        self._msg_box = tk.Text(msg_frame, height=8, wrap="word")
        msg_scroll = ttk.Scrollbar(msg_frame, orient="vertical", command=self._msg_box.yview)
        self._msg_box.configure(yscrollcommand=msg_scroll.set)
        self._msg_box.grid(row=0, column=0, sticky="nsew")
        msg_scroll.grid(row=0, column=1, sticky="ns")

    def _create_channel_panel(self, parent: tk.Misc, *, channel_name: str) -> ttk.LabelFrame:
        """
        Baut einen kompletten Bedienblock fuer genau einen Kanal.

        Der Block enthaelt:
        - Sollwert-Bedienung
        - EIN/AUS-Tasten
        - Istwert-Anzeige
        """

        frame = ttk.LabelFrame(parent, text=f"Kanal {channel_name}", padding=10)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        # ---------------- Tk-Variablen fuer Bedienung (Sollwerte) ----------------
        mode_var = tk.StringVar(value=RegulationMode.CURRENT.value)
        setpoint_var = tk.StringVar(value="1.0")
        pulse_freq_var = tk.StringVar(value="0")
        pulse_reverse_var = tk.StringVar(value="0.0")

        # ---------------- Tk-Variablen fuer Istwerte ----------------
        output_state_var = tk.StringVar(value="Output Soll OFF")
        actual_mode_var = tk.StringVar(value="Mode (Ist): -")
        actual_setpoint_var = tk.StringVar(value="Setpoint (Ist): -")
        actual_frequency_var = tk.StringVar(value="Pulse Frequency (Ist): -")
        actual_reverse_var = tk.StringVar(value="Pulse Reverse (Ist): -")
        actual_voltage_var = tk.StringVar(value="Voltage: -")
        actual_current_var = tk.StringVar(value="Current: -")
        actual_power_var = tk.StringVar(value="Power: -")

        # ---------------- Bedienzeile: Output EIN/AUS ----------------
        output_badge = tk.Label(
            frame,
            textvariable=output_state_var,
            font=("TkDefaultFont", 11, "bold"),
            fg="white",
            bg="#7f1d1d",
            padx=10,
            pady=4,
            anchor="w",
        )
        output_badge.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Button(
            frame,
            text="Output EIN",
            command=lambda: self._set_channel_output(channel_name, True),
        ).grid(row=0, column=2, sticky="ew", padx=(6, 4), pady=(0, 8))

        ttk.Button(
            frame,
            text="Output AUS",
            command=lambda: self._set_channel_output(channel_name, False),
        ).grid(row=0, column=3, sticky="ew", padx=(4, 0), pady=(0, 8))

        # ---------------- Sollwerte ----------------
        ttk.Label(frame, text="Regelmodus (Soll):").grid(row=1, column=0, sticky="w", pady=2)
        mode_combo = ttk.Combobox(
            frame,
            textvariable=mode_var,
            values=[RegulationMode.POWER.value, RegulationMode.VOLTAGE.value, RegulationMode.CURRENT.value],
            state="readonly",
        )
        mode_combo.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(frame, text="Setpoint (Soll):").grid(row=2, column=0, sticky="w", pady=2)
        setpoint_entry = ttk.Entry(frame, textvariable=setpoint_var)
        setpoint_entry.grid(row=2, column=1, sticky="ew", pady=2)

        # Original-C++-Konzept: Pulsfrequenz in 5-kHz-Schritten.
        ttk.Label(frame, text="Pulse Frequency [kHz] (Soll):").grid(row=3, column=0, sticky="w", pady=2)
        pulse_freq_combo = ttk.Combobox(
            frame,
            textvariable=pulse_freq_var,
            values=[str(v) for v in range(0, 101, 5)],
            state="readonly",
        )
        pulse_freq_combo.grid(row=3, column=1, sticky="ew", pady=2)

        # Original-C++-Konzept: Puls-Umkehrzeit in 0.1-us-Schritten.
        ttk.Label(frame, text="Pulse Reverse [us] (Soll):").grid(row=4, column=0, sticky="w", pady=2)
        pulse_reverse_combo = ttk.Combobox(
            frame,
            textvariable=pulse_reverse_var,
            values=[f"{i/10:.1f}" for i in range(0, 51)],
            state="readonly",
        )
        pulse_reverse_combo.grid(row=4, column=1, sticky="ew", pady=2)

        ttk.Button(
            frame,
            text="Sollwerte uebernehmen",
            command=lambda: self._apply_channel_settings(channel_name),
        ).grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 10))

        # ---------------- Istwerte ----------------
        status_box = ttk.LabelFrame(frame, text="Istwerte", padding=8)
        status_box.grid(row=6, column=0, columnspan=4, sticky="nsew")
        status_box.columnconfigure(0, weight=1)

        ttk.Label(status_box, textvariable=actual_mode_var).grid(row=0, column=0, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_setpoint_var).grid(row=1, column=0, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_frequency_var).grid(row=2, column=0, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_reverse_var).grid(row=3, column=0, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_voltage_var).grid(row=4, column=0, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_current_var).grid(row=5, column=0, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_power_var).grid(row=6, column=0, sticky="w", pady=1)

        # Alles fuer den Kanal im Dictionary merken.
        self._channel_ui[channel_name] = {
            "mode_var": mode_var,
            "setpoint_var": setpoint_var,
            "pulse_freq_var": pulse_freq_var,
            "pulse_reverse_var": pulse_reverse_var,
            "output_state_var": output_state_var,
            "output_badge": output_badge,
            "actual_mode_var": actual_mode_var,
            "actual_setpoint_var": actual_setpoint_var,
            "actual_frequency_var": actual_frequency_var,
            "actual_reverse_var": actual_reverse_var,
            "actual_voltage_var": actual_voltage_var,
            "actual_current_var": actual_current_var,
            "actual_power_var": actual_power_var,
            "setpoint_entry": setpoint_entry,
            "mode_combo": mode_combo,
            "pulse_freq_combo": pulse_freq_combo,
            "pulse_reverse_combo": pulse_reverse_combo,
        }

        return frame

    # ------------------------------------------------------------------
    # Bedienlogik
    # ------------------------------------------------------------------
    def _apply_channel_settings(self, channel_name: str) -> None:
        """
        Uebernimmt die Sollwerte aus den GUI-Feldern in den Controller-State.

        Wichtiger Ablauf:
        - Wir validieren die Eingaben lokal (Fehler -> klare Meldung).
        - Dann schreiben wir normierte Sollwerte in den Controller-State.
        - Der zentrale Tick-Pfad uebernimmt danach den echten Seriell-Write.
        """

        ui = self._channel_ui[channel_name]

        try:
            # 1) Rohtexte aus Tk-Feldern lesen.
            mode_text = str(self._get_var(ui, "mode_var").get())
            setpoint_value = float(str(self._get_var(ui, "setpoint_var").get()).strip())
            pulse_freq_khz = float(str(self._get_var(ui, "pulse_freq_var").get()).strip())
            pulse_reverse_us = float(str(self._get_var(ui, "pulse_reverse_var").get()).strip())
        except Exception as exc:
            messagebox.showerror(
                "Ungueltige Pinnacle-Eingabe",
                f"Bitte pruefe die Eingabefelder fuer Kanal {channel_name}.\nTechnischer Hinweis: {exc}",
                parent=self,
            )
            return

        # 2) Werte in den zentralen Controller schreiben.
        #    Der Controller normalisiert/validiert die Felder erneut.
        self._controller.set_pinnacle_channel_mode(channel_name, mode_text)
        self._controller.set_pinnacle_channel_setpoint(channel_name, setpoint_value)
        self._controller.set_pinnacle_channel_pulse_frequency_khz(channel_name, pulse_freq_khz)
        self._controller.set_pinnacle_channel_pulse_reverse_us(channel_name, pulse_reverse_us)

        # 3) Nur Bedienlog; physischer Write erfolgt im naechsten Tick.
        self._log(
            f"Kanal {channel_name}: Sollwerte uebernommen "
            f"(mode={mode_text}, setpoint={setpoint_value}, freq={pulse_freq_khz} kHz, reverse={pulse_reverse_us} us)."
        )

    def _build_runtime_from_form(self) -> "RuntimeSettings":
        runtime = self._runtime_settings_snapshot()
        mode_token = self._mode_select_var.get().strip().lower()
        simulation = mode_token != "real"
        return runtime.with_simulation(simulation)

    def _apply_mode(self) -> None:
        if self._apply_runtime_settings is None:
            messagebox.showerror("Nicht verfuegbar", "Mode-Switch ist ohne Runtime-Callback nicht verfuegbar.", parent=self)
            return

        try:
            settings = self._build_runtime_from_form()
            current = self._runtime_settings_snapshot()
            if settings.simulation == current.simulation:
                self._log(f"Modus bleibt unveraendert ({'simulation' if current.simulation else 'real'}).")
                return
            self._apply_runtime_settings(settings)
            self._log(f"Modus angewendet: {'simulation' if settings.simulation else 'real'}")
            self._log("Runtime gewechselt. Bitte Backend-Verbindung und Sollwerte pruefen.")
        except Exception as exc:
            messagebox.showerror("Moduswechsel fehlgeschlagen", str(exc), parent=self)
            self._log(f"Moduswechsel fehlgeschlagen: {exc}")

    def _update_mode_button_style(self) -> None:
        if not hasattr(self, "_mode_apply_button"):
            return

        selected = self._mode_select_var.get().strip().lower()
        if selected == "real":
            self._mode_apply_button.configure(
                text="REAL anwenden",
                bg="#fecaca",
                fg="#7f1d1d",
                activebackground="#fca5a5",
                activeforeground="#7f1d1d",
            )
        else:
            self._mode_apply_button.configure(
                text="SIM anwenden",
                bg="#dbeafe",
                fg="#0b3b66",
                activebackground="#bfdbfe",
                activeforeground="#0b3b66",
            )

    @staticmethod
    def _duplicate_ports(ports: dict[str, str]) -> dict[str, list[str]]:
        reverse: dict[str, list[str]] = {}
        for device, port in ports.items():
            token = str(port).strip()
            if not token:
                continue
            reverse.setdefault(token, []).append(device)
        return {port: keys for port, keys in reverse.items() if len(keys) > 1}

    def _update_port_conflict_info(self) -> None:
        runtime = self._runtime_settings_snapshot()
        duplicates = self._duplicate_ports(runtime.ports)
        if not duplicates:
            self._port_conflict_var.set("")
            return
        parts = [f"{port}:{'/'.join(sorted(keys))}" for port, keys in duplicates.items()]
        self._port_conflict_var.set("Port-Konflikt: " + "; ".join(parts))

    def _refresh_serial_ports(self) -> None:
        if self._list_serial_ports_cb is None:
            self._log("Ports scannen nicht verfuegbar (kein Callback).")
            return
        ports = self._list_serial_ports_cb()
        try:
            self._port_combo.configure(values=ports)
        except Exception:
            pass
        if ports:
            self._log("Serielle Ports: " + ", ".join(ports))
        else:
            self._log("Serielle Ports: keine gefunden.")

    def _apply_port_and_mode(self) -> None:
        if self._apply_runtime_settings is None:
            messagebox.showerror("Nicht verfuegbar", "Runtime-Callback fehlt.", parent=self)
            return
        runtime = self._runtime_settings_snapshot()
        mode_token = self._mode_select_var.get().strip().lower()
        simulation = mode_token != "real"
        ports = dict(runtime.ports)
        ports["pinnacle"] = self._serial_port_var.get().strip()
        duplicates = self._duplicate_ports(ports)
        if duplicates:
            txt = "; ".join(f"{port} -> {', '.join(sorted(keys))}" for port, keys in duplicates.items())
            if not messagebox.askyesno(
                "Portkonflikt",
                (
                    "Mehrere Backends teilen denselben Port:\n\n"
                    f"{txt}\n\n"
                    "Trotzdem anwenden?"
                ),
                parent=self,
            ):
                return
        new_runtime = replace(runtime, simulation=simulation, ports=ports)
        self._apply_runtime_settings(new_runtime)
        self._log(
            f"Runtime angewendet: mode={'simulation' if simulation else 'real'}, pinnacle-port={ports['pinnacle']}"
        )

    def _apply_serial_live(self) -> None:
        try:
            self._controller.set_pinnacle_serial_settings(
                port=self._serial_port_var.get().strip(),
                baudrate=int(self._serial_baud_var.get().strip()),
                parity=self._serial_parity_var.get().strip().upper(),
                timeout=float(self._serial_timeout_var.get().strip()),
                bytesize=int(self._serial_bytesize_var.get().strip()),
                stopbits=int(self._serial_stopbits_var.get().strip()),
            )
            self._log("Pinnacle-Serialparameter live angewendet.")
        except Exception as exc:
            messagebox.showerror("Serial-Parameter ungueltig", str(exc), parent=self)
            self._log(f"Serial-Parameter konnten nicht gesetzt werden: {exc}")

    def _apply_runtime_tuning(self) -> None:
        try:
            retries = int(self._query_retries_var.get().strip())
            delay_ms = float(self._command_delay_ms_var.get().strip())
            read_size = int(self._response_read_size_var.get().strip())
        except Exception as exc:
            messagebox.showerror("Kompatibilitaetsoptionen ungueltig", str(exc), parent=self)
            return

        self._controller.set_pinnacle_runtime_options(
            strict_protocol=bool(self._strict_protocol_var.get()),
            verify_after_apply=bool(self._verify_apply_var.get()),
            query_retries=max(0, retries),
            command_delay_s=max(0.0, delay_ms / 1000.0),
            response_read_size=max(8, read_size),
            fast_emergency_off=bool(self._fast_emergency_off_var.get()),
        )
        self._log(
            "Pinnacle-Kompatibilitaet gesetzt: "
            f"strict={self._strict_protocol_var.get()}, verify={self._verify_apply_var.get()}, "
            f"retries={max(0, retries)}, delay_ms={max(0.0, delay_ms):.1f}, read_size={max(8, read_size)}, "
            f"fast_off={self._fast_emergency_off_var.get()}"
        )

    def _apply_channel_addresses(self) -> None:
        try:
            addr_a = int(self._addr_a_var.get().strip())
            addr_b = int(self._addr_b_var.get().strip())
            self._controller.set_pinnacle_channel_address("A", addr_a)
            self._controller.set_pinnacle_channel_address("B", addr_b)
        except Exception as exc:
            messagebox.showerror("Pinnacle-Adressen ungueltig", str(exc), parent=self)
            self._log(f"Adressupdate fehlgeschlagen: {exc}")
            return
        self._log(f"Pinnacle-Adressen gesetzt: A={addr_a}, B={addr_b}")

    def _ping_channels(self) -> None:
        result = self._controller.ping_pinnacle_channels()
        self._log("Pinnacle Ping: " + ", ".join(f"{ch}={'ok' if ok else 'fail'}" for ch, ok in result.items()))

    def _set_channel_output(self, channel_name: str, on: bool) -> None:
        """
        Schaltet den gewuenschten Kanal EIN/AUS.

        Sicherheitsmassnahme:
        - Beim Einschalten fragen wir immer nach einer bewussten Bestaetigung.
        """

        if on and self._confirm_on_var.get():
            # Bewusste Benutzerbestaetigung vor HV-relevanter Aktion.
            confirmed = messagebox.askyesno(
                "Output einschalten bestaetigen",
                (
                    f"Kanal {channel_name} wird auf OUTPUT EIN gesetzt.\n\n"
                    "Bitte nur bestaetigen, wenn die Prozessbedingungen geprueft sind "
                    "(Vakuum, Zielmaterial, Interlocks, Verdrahtung)."
                ),
                parent=self,
            )
            if not confirmed:
                return

        # Setzt nur den Sollstatus; der Tick uebernimmt den Write.
        self._controller.set_pinnacle_channel_active(channel_name, on)
        state_txt = "EIN" if on else "AUS"
        self._log(f"Kanal {channel_name}: Output-Sollzustand -> {state_txt}")

    def _emergency_off_all(self) -> None:
        """
        Fail-safe-Aktion: setzt beide Pinnacle-Kanaele logisch sofort auf OFF.

        Die physische Uebertragung erfolgt im naechsten Controller-Tick.
        """

        confirmed = messagebox.askyesno(
            "NOT-AUS Pinnacle",
            "Wirklich beide Pinnacle-Kanaele auf AUS setzen?",
            parent=self,
        )
        if not confirmed:
            return

        self._controller.emergency_pinnacle_off_all()
        self._log("NOT-AUS ausgefuehrt: Kanal A und B wurden auf Output AUS gesetzt.")

    def _load_control_values_from_state(self) -> None:
        """
        Uebernimmt die aktuellen State-Sollwerte in die GUI-Eingabefelder.

        Das ist hilfreich, wenn Werte extern geaendert wurden und man die
        Eingabefelder wieder synchronisieren moechte.
        """

        mapping = {
            "A": self._controller.state.pin_a,
            "B": self._controller.state.pin_b,
        }

        for channel_name, state_channel in mapping.items():
            ui = self._channel_ui.get(channel_name)
            if ui is None:
                continue
            self._get_var(ui, "mode_var").set(state_channel.mode.value)
            self._get_var(ui, "setpoint_var").set(f"{state_channel.setpoint:.3f}")
            self._get_var(ui, "pulse_freq_var").set(str(state_channel.pulse_frequency_index * 5))
            self._get_var(ui, "pulse_reverse_var").set(f"{state_channel.pulse_reverse_index * 0.1:.1f}")

        self._log("GUI-Sollfelder aus aktuellem Controller-State geladen.")

    def _reconnect_pinnacle_backend(self) -> None:
        """
        Stoesst einen expliziten Pinnacle-Reconnect ueber den Controller an.

        Diese Funktion bietet denselben Bediengedanken wie die alte C++-Taste
        "Port connected", aber ohne unsichere Parallelzugriffe auf den Port.
        """

        ok = self._controller.reconnect_pinnacle()
        if ok:
            self._log("Pinnacle-Backend reconnect erfolgreich.")
        else:
            self._log("Pinnacle-Backend reconnect fehlgeschlagen. Details im Hauptlog.")

    def _apply_plot_settings(self) -> None:
        """
        Liest und validiert die Plot-Einstellfelder.

        Danach werden die internen, validierten Konfigurationswerte gesetzt und
        sofort auf die sichtbare Darstellung angewendet.
        """

        try:
            a_min = float(self._plot_a_ymin_var.get().strip())
            a_max = float(self._plot_a_ymax_var.get().strip())
            b_min = float(self._plot_b_ymin_var.get().strip())
            b_max = float(self._plot_b_ymax_var.get().strip())
            window_s = float(self._plot_window_s_var.get().strip())
        except Exception as exc:
            messagebox.showerror(
                "Pinnacle Plot-Optionen ungueltig",
                f"Die Plot-Optionen konnten nicht gelesen werden: {exc}",
                parent=self,
            )
            return

        if a_max <= a_min:
            messagebox.showerror(
                "Pinnacle Plot-Optionen ungueltig",
                "Kanal A: Y max muss groesser als Y min sein.",
                parent=self,
            )
            return
        if b_max <= b_min:
            messagebox.showerror(
                "Pinnacle Plot-Optionen ungueltig",
                "Kanal B: Y max muss groesser als Y min sein.",
                parent=self,
            )
            return
        if window_s < 0.0:
            messagebox.showerror(
                "Pinnacle Plot-Optionen ungueltig",
                "Das Zeitfenster darf nicht negativ sein. 0 bedeutet: kompletter Puffer.",
                parent=self,
            )
            return
        if self._plot_y_log_var.get() and (a_min <= 0.0 or b_min <= 0.0):
            messagebox.showerror(
                "Pinnacle Plot-Optionen ungueltig",
                "Im Y-Log-Modus muessen A min und B min > 0 sein.",
                parent=self,
            )
            return

        self._plot_use_log = bool(self._plot_y_log_var.get())
        self._plot_use_minor_grid = bool(self._plot_minor_grid_var.get())
        self._plot_keep_a_limits = bool(self._plot_keep_a_limits_var.get())
        self._plot_keep_b_limits = bool(self._plot_keep_b_limits_var.get())
        self._plot_a_ymin = a_min
        self._plot_a_ymax = a_max
        self._plot_b_ymin = b_min
        self._plot_b_ymax = b_max
        self._plot_window_seconds = window_s

        self._log(
            "Pinnacle-Plotoptionen aktualisiert: "
            f"ylog={self._plot_use_log}, minor={self._plot_use_minor_grid}, "
            f"A_fixed={self._plot_keep_a_limits}[{self._plot_a_ymin:.2f},{self._plot_a_ymax:.2f}], "
            f"B_fixed={self._plot_keep_b_limits}[{self._plot_b_ymin:.2f},{self._plot_b_ymax:.2f}], "
            f"window={self._plot_window_seconds:.1f}s"
        )
        self._refresh_plot()

    # ------------------------------------------------------------------
    # Tick-Schnittstelle aus der Haupt-GUI
    # ------------------------------------------------------------------
    def on_state_tick(self, state: PlantState) -> None:
        """
        Wird vom Hauptfenster bei jedem Tick aufgerufen.

        Aufgaben pro Tick:
        1) Backend-Status aktualisieren
        2) Istwerte pro Kanal aktualisieren
        3) Plotdaten erweitern
        """

        runtime_cfg = self._runtime_settings_snapshot()
        mode_token = "simulation" if runtime_cfg.simulation else "real"
        if self._mode_select_var.get() != mode_token:
            self._mode_select_var.set(mode_token)
        self._mode_var.set("Simulation" if runtime_cfg.simulation else "Real hardware")
        self._mode_badge_var.set(self._mode_badge_text(runtime_cfg.simulation))
        if hasattr(self, "_mode_badge_label"):
            self._mode_badge_label.configure(bg="#1f6f43" if runtime_cfg.simulation else "#7f1d1d")
        self._update_mode_button_style()
        self._update_port_conflict_info()

        port_runtime = state.ports.get("pinnacle")
        if port_runtime is None:
            self._backend_health_var.set("pinnacle backend: unknown")
        else:
            # Gemeinsamer Port-Health-String aus dem Controller.
            self._backend_health_var.set(
                f"pinnacle backend: connected={port_runtime.connected}, failed={port_runtime.failed}, error='{port_runtime.last_error}'"
            )

        # Wir nutzen fuer das Alter denselben Zeitstempel wie im Vakuumteil,
        # weil die Tick-Zeitskala systemweit zentral ist.
        last_ts = state.vacuum.last_update_monotonic_s
        if last_ts <= 0.0:
            self._last_update_age_var.set("Age: noch kein gemeinsamer Tick-Zeitstempel")
        else:
            age = max(0.0, time.monotonic() - last_ts)
            self._last_update_age_var.set(f"Age: {age:.2f} s seit letztem Druck-/Backend-Tick")

        self._update_channel_readback("A", state.pin_a)
        self._update_channel_readback("B", state.pin_b)

        # Plot arbeitet ausschliesslich auf bereits gelesenen Istwerten.
        self._update_plot(state.pin_a.voltage, state.pin_b.voltage)

    def _update_channel_readback(self, channel_name: str, channel: PinnacleChannelState) -> None:
        """
        Schreibt alle Istwerte eines Kanals in die Labelvariablen.
        """

        ui = self._channel_ui.get(channel_name)
        if ui is None:
            return

        output_text = "ON" if channel.active else "OFF"
        comm_text = "COMM OK" if channel.comm_ok else "COMM ERR"
        self._get_var(ui, "output_state_var").set(f"Output Soll {output_text} | {comm_text}")
        output_badge = ui.get("output_badge")
        if isinstance(output_badge, tk.Label):
            # Farbregel:
            # - Orange: Kommunikationsproblem
            # - Gruen: Output EIN
            # - Rot: Output AUS
            if not channel.comm_ok:
                output_badge.configure(bg="#92400e")
            elif channel.active:
                output_badge.configure(bg="#166534")
            else:
                output_badge.configure(bg="#7f1d1d")
        mode_line = f"Mode (Ist): {channel.regulation} (code={int(channel.act_regulation_mode_code)})"
        if (not channel.comm_ok) and channel.last_error:
            mode_line += f" | last_err={channel.last_error}"
        self._get_var(ui, "actual_mode_var").set(mode_line)
        self._get_var(ui, "actual_setpoint_var").set(f"Setpoint (Ist): {channel.setpoint_actual:.3f}")
        self._get_var(ui, "actual_frequency_var").set(f"Pulse Frequency (Ist): {channel.act_pulse_frequency:d} kHz")
        self._get_var(ui, "actual_reverse_var").set(
            f"Pulse Reverse (Ist): {channel.act_pulse_reverse_time:.1f} us"
        )
        self._get_var(ui, "actual_voltage_var").set(f"Voltage: {channel.voltage:.1f} V")
        self._get_var(ui, "actual_current_var").set(f"Current: {channel.current:.3f} A")
        self._get_var(ui, "actual_power_var").set(f"Power: {channel.power:.3f} W")

    # ------------------------------------------------------------------
    # Plot
    # ------------------------------------------------------------------
    def _initialize_plot_backend(self) -> None:
        """
        Initialisiert den Plotbereich fuer Pinnacle-Spannungen.

        Verhalten:
        - Mit matplotlib: zwei uebereinanderliegende Spannungsplots (A/B).
        - Ohne matplotlib: klare Hinweisnachricht, GUI bleibt voll nutzbar.
        """

        try:
            import matplotlib

            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            self._plot_modules = {
                "Figure": Figure,
                "FigureCanvasTkAgg": FigureCanvasTkAgg,
            }
            self._plot_available = True
        except Exception as exc:
            self._plot_available = False
            label = ttk.Label(
                self._plot_container,
                text=(
                    "Matplotlib ist nicht verfuegbar. Der Spannungsplot bleibt deaktiviert.\n"
                    "Die Pinnacle-Bedienung und Istwertanzeige funktionieren weiterhin.\n"
                    f"Technischer Hinweis: {exc}"
                ),
                justify="left",
            )
            label.pack(anchor="w", fill="x")
            self._log("Hinweis: Kein Matplotlib gefunden. Pinnacle-Plot ist deaktiviert.")
            return

        Figure = self._plot_modules["Figure"]
        FigureCanvasTkAgg = self._plot_modules["FigureCanvasTkAgg"]

        self._figure = Figure(figsize=(9.0, 5.2), dpi=100)
        self._ax_a = self._figure.add_subplot(211)
        self._ax_b = self._figure.add_subplot(212, sharex=self._ax_a)

        self._ax_a.set_title("Kanal A Spannung")
        self._ax_a.set_ylabel("V")
        self._ax_a.grid(True, alpha=0.35)

        self._ax_b.set_title("Kanal B Spannung")
        self._ax_b.set_xlabel("Zeit seit Fensterstart [s]")
        self._ax_b.set_ylabel("V")
        self._ax_b.grid(True, alpha=0.35)

        (self._line_a,) = self._ax_a.plot([], [], label="A", color="#1f77b4")
        (self._line_b,) = self._ax_b.plot([], [], label="B", color="#d62728")

        self._canvas = FigureCanvasTkAgg(self._figure, master=self._plot_container)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)
        self._canvas.draw_idle()

        # Initiale Synchronisierung der Achsen mit den konfigurierten Optionen.
        self._apply_plot_settings()

    def _update_plot(self, voltage_a: float, voltage_b: float) -> None:
        """
        Fuegt pro Tick genau einen Spannungsdatenpunkt hinzu.
        """

        now = time.monotonic()
        if self._plot_t0_monotonic is None:
            self._plot_t0_monotonic = now
        t_rel = now - self._plot_t0_monotonic

        self._plot_times_s.append(t_rel)
        self._plot_voltage_a.append(float(voltage_a))
        self._plot_voltage_b.append(float(voltage_b))

        # Speichergrenze fuer Langzeitbetrieb: letzte 20.000 Punkte behalten.
        if len(self._plot_times_s) > 20_000:
            self._plot_times_s = self._plot_times_s[-20_000:]
            self._plot_voltage_a = self._plot_voltage_a[-20_000:]
            self._plot_voltage_b = self._plot_voltage_b[-20_000:]

        self._sample_counter += 1
        self._sample_count_var.set(str(self._sample_counter))

        self._refresh_plot()

    def _refresh_plot(self) -> None:
        """
        Wendet die interne Datenliste plus Plotoptionen auf die Matplotlib-Achsen an.

        Diese Trennung (`_update_plot` vs. `_refresh_plot`) macht den Code klarer:
        - `_update_plot`: Datenpunkte sammeln
        - `_refresh_plot`: Darstellung konfigurieren/zeichnen
        """

        if not self._plot_available:
            return
        if self._line_a is None or self._line_b is None:
            return

        times = self._plot_times_s
        values_a = self._plot_voltage_a
        values_b = self._plot_voltage_b

        # Optionales Zeitfenster (0 = kompletter Puffer).
        if self._plot_window_seconds > 0.0 and times:
            t_cut = times[-1] - self._plot_window_seconds
            start_idx = 0
            while start_idx < len(times) and times[start_idx] < t_cut:
                start_idx += 1
            times = times[start_idx:]
            values_a = values_a[start_idx:]
            values_b = values_b[start_idx:]

        # Schutz fuer Y-Log:
        # Matplotlib kann keine <=0-Werte auf einer Log-Achse zeichnen.
        # Wir ersetzen diese Werte durch NaN, damit die Kurve an diesen Stellen
        # sauber "aussetzt", statt den kompletten Plot zu destabilisieren.
        if self._plot_use_log:
            values_a = [v if v > 0.0 else float("nan") for v in values_a]
            values_b = [v if v > 0.0 else float("nan") for v in values_b]

        self._line_a.set_data(times, values_a)
        self._line_b.set_data(times, values_b)

        if self._ax_a is not None:
            self._ax_a.set_yscale("log" if self._plot_use_log else "linear")
            self._ax_a.grid(True, which="both" if self._plot_use_minor_grid else "major", alpha=0.35)
            if self._plot_keep_a_limits:
                self._ax_a.set_ylim(self._plot_a_ymin, self._plot_a_ymax)
                self._ax_a.set_autoscaley_on(False)
            else:
                self._ax_a.set_autoscaley_on(True)
                self._ax_a.relim()
                self._ax_a.autoscale_view()

        if self._ax_b is not None:
            self._ax_b.set_yscale("log" if self._plot_use_log else "linear")
            self._ax_b.grid(True, which="both" if self._plot_use_minor_grid else "major", alpha=0.35)
            if self._plot_keep_b_limits:
                self._ax_b.set_ylim(self._plot_b_ymin, self._plot_b_ymax)
                self._ax_b.set_autoscaley_on(False)
            else:
                self._ax_b.set_autoscaley_on(True)
                self._ax_b.relim()
                self._ax_b.autoscale_view()

        if times:
            t0 = times[0]
            t1 = times[-1] if times[-1] > t0 else t0 + 1.0
            if self._ax_a is not None:
                self._ax_a.set_xlim(t0, t1)

        if self._canvas is not None:
            self._canvas.draw_idle()

    def _reset_plot_data(self) -> None:
        self._sample_counter = 0
        self._sample_count_var.set("0")
        self._plot_t0_monotonic = None
        self._plot_times_s.clear()
        self._plot_voltage_a.clear()
        self._plot_voltage_b.clear()
        if self._line_a is not None:
            self._line_a.set_data([], [])
        if self._line_b is not None:
            self._line_b.set_data([], [])
        self._refresh_plot()

    def set_controller(self, controller: "Controller") -> None:
        self._controller = controller
        runtime = self._runtime_settings_snapshot()
        self._mode_select_var.set("simulation" if runtime.simulation else "real")
        self._mode_var.set("Simulation" if runtime.simulation else "Real hardware")
        self._mode_badge_var.set(self._mode_badge_text(runtime.simulation))
        self._update_mode_button_style()
        self._serial_port_var.set(runtime.ports.get("pinnacle", ""))
        serial_cfg = self._controller.get_pinnacle_serial_settings()
        self._serial_baud_var.set(str(int(serial_cfg["baudrate"])))
        self._serial_parity_var.set(str(serial_cfg["parity"]).upper())
        self._serial_timeout_var.set(f"{float(serial_cfg['timeout']):.3f}")
        self._serial_bytesize_var.set(str(int(serial_cfg["bytesize"])))
        self._serial_stopbits_var.set(str(int(serial_cfg["stopbits"])))
        tuning_cfg = self._controller.get_pinnacle_runtime_options()
        self._strict_protocol_var.set(bool(tuning_cfg["strict_protocol"]))
        self._verify_apply_var.set(bool(tuning_cfg["verify_after_apply"]))
        self._fast_emergency_off_var.set(bool(tuning_cfg["fast_emergency_off"]))
        self._query_retries_var.set(str(int(tuning_cfg["query_retries"])))
        self._command_delay_ms_var.set(f"{float(tuning_cfg['command_delay_s']) * 1000.0:.0f}")
        self._response_read_size_var.set(str(int(tuning_cfg["response_read_size"])))
        self._backend_health_var.set("pinnacle backend: unknown")
        self._last_update_age_var.set("Age: -")
        self._load_control_values_from_state()
        self._addr_a_var.set(str(int(self._controller.state.pin_a.address)))
        self._addr_b_var.set(str(int(self._controller.state.pin_b.address)))
        self._update_port_conflict_info()
        self._refresh_serial_ports()
        self._reset_plot_data()
        self._log("Controller-Handle nach Runtime-Wechsel aktualisiert.")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _get_var(mapping: dict[str, object], key: str) -> tk.Variable:
        """
        Kleine Typhilfe fuer den Zugriff auf gespeicherte Tk-Variablen.
        """

        value = mapping.get(key)
        if isinstance(value, tk.Variable):
            return value
        raise RuntimeError(f"Missing Tk variable '{key}' in channel mapping")

    def _log(self, text: str) -> None:
        """
        Schreibt eine lokale Meldung mit Zeitstempel in das Meldungsfeld.
        """

        ts = datetime.now().strftime("%H:%M:%S")
        self._msg_box.insert("end", f"[{ts}] {text}\n")
        self._msg_box.see("end")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self.destroy()

    def close_window(self) -> None:
        """
        Oeffentliche Schliessmethode fuer das Hauptfenster.
        """

        self._on_close()


__all__ = ["PinnacleWindow"]
