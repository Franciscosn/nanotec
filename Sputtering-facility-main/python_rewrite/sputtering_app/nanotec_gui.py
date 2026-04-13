from __future__ import annotations

"""
Unter-GUI fuer die Nanotec-Schrittmotorsteuerung.

Ziel dieses Fensters:
- Zwei Motoren (Adresse 1 und 2) in einer klaren, sicheren Oberflaeche bedienbar
  machen.
- Die wichtigsten Funktionen aus dem Legacy-Bedienteil abbilden:
  - Sollwerte setzen (Speed, Zielposition, Step Mode, Richtung, Referenzrichtung,
    Wiederholungen)
  - Start / Stop / Referenzfahrt
  - Live-Rueckmeldung (Connected, Running, Statuscode, Position, Encoder, Laufzeit)
  - Taster-/Endschalteranzeige

Sicherheitsprinzipien in dieser GUI:
1) Sollwerte werden nicht direkt geschrieben, sondern immer ueber die zentrale
   Controller-API mit Validierung und Rollback.
2) Start-/Stop-/Referenzbefehle gehen ausschliesslich ueber den Controller.
3) Es gibt einen prominent sichtbaren "STOPP ALLE MOTOREN"-Button.
4) Das Fenster oeffnet keine eigenen seriellen Ports und verursacht daher keine
   Konkurrenzzugriffe auf COM-Ports.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

from .config import (
    MOTOR1_LEFT_TASTER_ACTIVE_LEVEL,
    MOTOR1_LEFT_TASTER_BIT,
    MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL,
    MOTOR1_RIGHT_TASTER_BIT,
    MOTOR2_LEFT_TASTER_ACTIVE_LEVEL,
    MOTOR2_LEFT_TASTER_BIT,
    MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL,
    MOTOR2_RIGHT_TASTER_BIT,
    NANOTEC_STEP_MODES,
)
from .models import MotorDirection, MotorState, PlantState

if TYPE_CHECKING:
    from .controller import Controller
    from .runtime_settings import RuntimeSettings


class NanotecWindow(tk.Toplevel):
    """
    Eigenstaendiges Fenster fuer die Schrittmotorsteuerung.

    Dieses Fenster wird aus der Haupt-GUI geoeffnet und bei jedem globalen Tick
    mit dem aktuellen PlantState aktualisiert.
    """

    def __init__(
        self,
        parent: tk.Misc,
        controller: "Controller",
        *,
        motor_order: tuple[int, int] = (1, 2),
        chamber_labels: dict[int, str] | None = None,
        get_runtime_settings: Callable[[], "RuntimeSettings"] | None = None,
        apply_runtime_settings: Callable[["RuntimeSettings"], None] | None = None,
        list_serial_ports_cb: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._get_runtime_settings = get_runtime_settings
        self._apply_runtime_settings = apply_runtime_settings
        self._list_serial_ports_cb = list_serial_ports_cb
        self._motor_order = motor_order if set(motor_order) == {1, 2} else (1, 2)
        self._chamber_labels = chamber_labels or {
            1: "Schleusenkammer",
            2: "Sputterkammer",
        }

        self.title("Schrittmotoren (Nanotec) - Enhanced")
        self.geometry("1420x900")
        self.minsize(1180, 760)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Kopfstatusvariablen.
        runtime = self._runtime_settings_snapshot()
        self._mode_var = tk.StringVar(value="Simulation" if runtime.simulation else "Real hardware")
        self._mode_select_var = tk.StringVar(value="simulation" if runtime.simulation else "real")
        self._mode_state_var = tk.StringVar(value="")
        self._mode_internal_change = False
        self._invert_m2_direction_var = tk.BooleanVar(value=True)
        self._port_var = tk.StringVar(value=str(runtime.ports.get("nanotec", "")).strip())
        self._port_state_var = tk.StringVar(value="Portstatus: unbekannt")
        self._address_m1_var = tk.StringVar(value=str(self._controller.state.motor1.address))
        self._address_m2_var = tk.StringVar(value=str(self._controller.state.motor2.address))
        self._backend_health_var = tk.StringVar(value="nanotec backend: unknown")
        self._override_banner_var = tk.StringVar(value="")

        self._port_dirty = False
        self._address_m1_dirty = False
        self._address_m2_dirty = False
        self._override_internal_change = False
        self._service_panel_visible = False

        self._service_mode_var = tk.BooleanVar(value=False)
        self._override_vars: dict[str, tk.BooleanVar] = {
            "allow_unknown_limit_inputs": tk.BooleanVar(value=False),
            "bypass_preflight_requirement": tk.BooleanVar(value=False),
            "bypass_active_limit_block_m1": tk.BooleanVar(value=False),
            "bypass_active_limit_block_m2": tk.BooleanVar(value=False),
            "bypass_soft_limit_block_m1": tk.BooleanVar(value=False),
            "bypass_soft_limit_block_m2": tk.BooleanVar(value=False),
        }
        self._override_widgets: dict[str, tk.Widget] = {}

        # Pro Motor speichern wir alle GUI-Elemente in einem Dictionary.
        # Dadurch ist der Update-Code fuer Motor 1 und Motor 2 identisch.
        self._motor_ui: dict[int, dict[str, object]] = {}
        self._step_zero_offset: dict[int, int] = {1: 0, 2: 0}
        self._range_running: dict[int, bool] = {1: False, 2: False}
        self._reference_zero_latched: dict[int, bool] = {1: False, 2: False}
        self._last_encoder_mm: dict[int, float] = {1: 0.0, 2: 0.0}
        self._shaft_moving: dict[int, bool] = {1: False, 2: False}
        self._cnc_status_vars: dict[int, dict[str, tk.StringVar]] = {
            1: {
                "motor": tk.StringVar(value="M1 Motor: -"),
                "shaft": tk.StringVar(value="M1 Welle: -"),
                "position": tk.StringVar(value="M1 Position: -"),
            },
            2: {
                "motor": tk.StringVar(value="M2 Motor: -"),
                "shaft": tk.StringVar(value="M2 Welle: -"),
                "position": tk.StringVar(value="M2 Position: -"),
            },
        }

        self._build_ui()
        self._refresh_ports()
        self._update_mode_indicator()
        self._sync_override_controls_from_controller()
        self._load_motor_fields_from_state(1)
        self._load_motor_fields_from_state(2)
        self._log("Nanotec-Fenster geoeffnet. Bedienung laeuft ueber den zentralen Controller.")

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """
        Baut die komplette Fensterstruktur auf.

        Aufbau:
        - Header: Modus, Backendzustand, globale Aktionen
        - Mitte: Zwei Motor-Panels nebeneinander
        - Unten: Meldungsbereich
        """

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.LabelFrame(outer, text="Nanotec: Status und globale Aktionen", padding=8)
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=1)
        header.columnconfigure(2, weight=1)

        mode_top = ttk.Frame(header)
        mode_top.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12), pady=2)
        ttk.Label(mode_top, text="Betriebsmodus:", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        self._mode_light = self._create_indicator(mode_top)
        self._mode_light.grid(row=0, column=1, sticky="w", padx=(6, 8))
        mode_cb = ttk.Combobox(
            mode_top,
            textvariable=self._mode_select_var,
            values=["simulation", "real"],
            state="readonly",
            width=12,
        )
        mode_cb.grid(row=0, column=2, sticky="w")
        mode_cb.bind("<<ComboboxSelected>>", self._on_mode_selection_changed)

        self._mode_apply_button = tk.Button(
            mode_top,
            text="SIM anwenden",
            command=self._apply_mode,
            bg="#dbeafe",
            fg="#0b3b66",
            activebackground="#bfdbfe",
            relief="raised",
            bd=1,
            padx=10,
            pady=3,
        )
        self._mode_apply_button.grid(row=0, column=3, sticky="w", padx=(8, 8))

        self._mode_state_label = ttk.Label(mode_top, textvariable=self._mode_state_var, width=18, anchor="w")
        self._mode_state_label.grid(row=0, column=4, sticky="w")

        ttk.Label(mode_top, text="Aktuell:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(mode_top, textvariable=self._mode_var, font=("TkDefaultFont", 10, "bold")).grid(
            row=1,
            column=2,
            columnspan=3,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Checkbutton(
            mode_top,
            text="M2 Richtung invertiert",
            variable=self._invert_m2_direction_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Label(header, text="Backend:").grid(row=0, column=1, sticky="e", padx=(12, 6), pady=2)
        ttk.Label(header, textvariable=self._backend_health_var).grid(row=0, column=2, sticky="w", pady=2)

        ttk.Button(header, text="Nanotec neu verbinden", command=self._reconnect_nanotec).grid(
            row=1, column=1, sticky="ew", padx=(12, 4), pady=4
        )
        ttk.Button(header, text="STOPP ALLE MOTOREN", command=self._stop_all_motors).grid(
            row=1, column=2, sticky="ew", padx=(4, 0), pady=4
        )

        address_row = ttk.Frame(header)
        address_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        address_row.columnconfigure(7, weight=1)
        ttk.Label(address_row, text="M1 Adresse:").grid(row=0, column=0, sticky="w")
        addr1_entry = ttk.Entry(address_row, textvariable=self._address_m1_var, width=8)
        addr1_entry.grid(row=0, column=1, sticky="w", padx=(4, 10))
        addr1_entry.bind("<KeyRelease>", lambda _e: self._mark_address_dirty(1))
        ttk.Label(address_row, text="M2 Adresse:").grid(row=0, column=2, sticky="w")
        addr2_entry = ttk.Entry(address_row, textvariable=self._address_m2_var, width=8)
        addr2_entry.grid(row=0, column=3, sticky="w", padx=(4, 10))
        addr2_entry.bind("<KeyRelease>", lambda _e: self._mark_address_dirty(2))
        ttk.Button(address_row, text="Adressen uebernehmen", command=self._apply_motor_addresses).grid(
            row=0, column=4, sticky="ew", padx=(6, 0)
        )

        port_row = ttk.Frame(header)
        port_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        for col in range(7):
            port_row.columnconfigure(col, weight=1 if col in {1, 5} else 0)

        ttk.Label(port_row, text="Nanotec-Port:").grid(row=0, column=0, sticky="w")
        self._port_cb = ttk.Combobox(port_row, textvariable=self._port_var, state="readonly", width=16)
        self._port_cb.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        self._port_cb.bind("<<ComboboxSelected>>", self._on_port_selected)
        ttk.Button(port_row, text="Ports aktualisieren", command=self._refresh_ports).grid(
            row=0, column=2, sticky="ew", padx=(0, 8)
        )
        self._port_apply_button = ttk.Button(port_row, text="Port verbinden", command=self._toggle_port_connection)
        self._port_apply_button.grid(row=0, column=3, sticky="ew", padx=(0, 10))
        ttk.Label(port_row, text="Ready:").grid(row=0, column=4, sticky="e")
        self._port_ready_light = self._create_indicator(port_row)
        self._port_ready_light.grid(row=0, column=5, sticky="w", padx=(6, 8))
        ttk.Label(port_row, textvariable=self._port_state_var).grid(row=0, column=6, sticky="w")

        taster_text = (
            "Taster-Mapping: "
            f"M1 links={MOTOR1_LEFT_TASTER_BIT}(active={MOTOR1_LEFT_TASTER_ACTIVE_LEVEL}), "
            f"M1 rechts={MOTOR1_RIGHT_TASTER_BIT}(active={MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL}), "
            f"M2 links={MOTOR2_LEFT_TASTER_BIT}(active={MOTOR2_LEFT_TASTER_ACTIVE_LEVEL}), "
            f"M2 rechts={MOTOR2_RIGHT_TASTER_BIT}(active={MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL})"
        )
        ttk.Label(header, text=taster_text).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # Zusaetzliche Safety-Transparenz:
        # Wir zeigen die konfigurierten Software-Fahrgrenzen direkt im Header,
        # damit Anwender sofort sehen, ob und wie die zweite Schutzebene aktiv ist.
        ttk.Label(header, text=self._soft_limit_summary_text()).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(2, 0),
        )

        service_row = ttk.Frame(header)
        service_row.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        service_row.columnconfigure(1, weight=1)
        self._service_toggle_button = ttk.Button(service_row, text="Service/Test anzeigen", command=self._toggle_service_panel)
        self._service_toggle_button.grid(row=0, column=0, sticky="w")
        self._override_banner_label = tk.Label(
            service_row,
            textvariable=self._override_banner_var,
            fg="#7f1d1d",
            bg=self.cget("bg"),
            font=("TkDefaultFont", 10, "bold"),
        )
        self._override_banner_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

        self._service_panel = ttk.LabelFrame(header, text="Service/Test Overrides", padding=8)
        self._service_panel.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._service_panel.columnconfigure(0, weight=1)
        self._service_panel.columnconfigure(1, weight=1)
        self._service_panel.grid_remove()

        ttk.Checkbutton(
            self._service_panel,
            text="Service-Modus aktivieren (unsicher)",
            variable=self._service_mode_var,
            command=self._on_service_mode_toggled,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        tk.Label(
            self._service_panel,
            text=(
                "Achtung: Overrides sind nur fuer kontrollierte Tests. "
                "Ohne Service-Modus bleiben alle Overrides wirkungslos."
            ),
            fg="#8a3000",
            bg=self.cget("bg"),
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self._override_widgets["allow_unknown_limit_inputs"] = ttk.Checkbutton(
            self._service_panel,
            text="allow_unknown_limit_inputs",
            variable=self._override_vars["allow_unknown_limit_inputs"],
            command=lambda: self._on_override_toggled("allow_unknown_limit_inputs"),
        )
        self._override_widgets["allow_unknown_limit_inputs"].grid(row=2, column=0, sticky="w", pady=1)

        self._override_widgets["bypass_preflight_requirement"] = ttk.Checkbutton(
            self._service_panel,
            text="bypass_preflight_requirement",
            variable=self._override_vars["bypass_preflight_requirement"],
            command=lambda: self._on_override_toggled("bypass_preflight_requirement"),
        )
        self._override_widgets["bypass_preflight_requirement"].grid(row=2, column=1, sticky="w", pady=1)

        self._override_widgets["bypass_active_limit_block_m1"] = ttk.Checkbutton(
            self._service_panel,
            text="bypass_active_limit_block_m1",
            variable=self._override_vars["bypass_active_limit_block_m1"],
            command=lambda: self._on_override_toggled("bypass_active_limit_block_m1"),
        )
        self._override_widgets["bypass_active_limit_block_m1"].grid(row=3, column=0, sticky="w", pady=1)

        self._override_widgets["bypass_active_limit_block_m2"] = ttk.Checkbutton(
            self._service_panel,
            text="bypass_active_limit_block_m2",
            variable=self._override_vars["bypass_active_limit_block_m2"],
            command=lambda: self._on_override_toggled("bypass_active_limit_block_m2"),
        )
        self._override_widgets["bypass_active_limit_block_m2"].grid(row=3, column=1, sticky="w", pady=1)

        self._override_widgets["bypass_soft_limit_block_m1"] = ttk.Checkbutton(
            self._service_panel,
            text="bypass_soft_limit_block_m1",
            variable=self._override_vars["bypass_soft_limit_block_m1"],
            command=lambda: self._on_override_toggled("bypass_soft_limit_block_m1"),
        )
        self._override_widgets["bypass_soft_limit_block_m1"].grid(row=4, column=0, sticky="w", pady=1)

        self._override_widgets["bypass_soft_limit_block_m2"] = ttk.Checkbutton(
            self._service_panel,
            text="bypass_soft_limit_block_m2",
            variable=self._override_vars["bypass_soft_limit_block_m2"],
            command=lambda: self._on_override_toggled("bypass_soft_limit_block_m2"),
        )
        self._override_widgets["bypass_soft_limit_block_m2"].grid(row=4, column=1, sticky="w", pady=1)

        ttk.Button(
            self._service_panel,
            text="Alle Overrides zuruecksetzen",
            command=self._reset_overrides_clicked,
        ).grid(row=5, column=0, sticky="w", pady=(8, 0))

        cnc_panel = ttk.LabelFrame(outer, text="CNC-Status (Live)", padding=8)
        cnc_panel.pack(fill="x", pady=(8, 0))
        for col in range(2):
            cnc_panel.columnconfigure(col, weight=1)
        for idx, motor_index in enumerate((1, 2)):
            block = ttk.Frame(cnc_panel)
            block.grid(row=0, column=idx, sticky="ew", padx=(0, 8) if idx == 0 else (8, 0))
            ttk.Label(block, textvariable=self._cnc_status_vars[motor_index]["motor"], font=("TkDefaultFont", 10, "bold")).grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(block, textvariable=self._cnc_status_vars[motor_index]["shaft"], font=("TkDefaultFont", 10, "bold")).grid(
                row=1, column=0, sticky="w"
            )
            ttk.Label(block, textvariable=self._cnc_status_vars[motor_index]["position"]).grid(row=2, column=0, sticky="w")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        for col, motor_index in enumerate(self._motor_order):
            panel = self._create_motor_panel(body, motor_index=motor_index)
            if col == 0:
                panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
            else:
                panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        msg_frame = ttk.LabelFrame(outer, text="Meldungen", padding=6)
        msg_frame.pack(fill="both", expand=False, pady=(10, 0))
        msg_frame.rowconfigure(0, weight=1)
        msg_frame.columnconfigure(0, weight=1)

        self._msg_box = tk.Text(msg_frame, height=8, wrap="word")
        msg_scroll = ttk.Scrollbar(msg_frame, orient="vertical", command=self._msg_box.yview)
        self._msg_box.configure(yscrollcommand=msg_scroll.set)
        self._msg_box.grid(row=0, column=0, sticky="nsew")
        msg_scroll.grid(row=0, column=1, sticky="ns")

    def _create_motor_panel(self, parent: tk.Misc, *, motor_index: int) -> ttk.LabelFrame:
        """
        Erzeugt den kompletten Bedien- und Statusbereich fuer einen Motor.

        Die Struktur ist absichtlich gleich fuer beide Motoren.
        """

        state = self._controller.state.motor1 if motor_index == 1 else self._controller.state.motor2

        chamber = self._chamber_labels.get(motor_index, "-")
        frame = ttk.LabelFrame(
            parent,
            text=f"Motor {motor_index} - {chamber} (Adresse {state.address})",
            padding=10,
        )
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        # ---------------- Sollwert-Bedienung ----------------
        control_box = ttk.LabelFrame(frame, text="Sollwerte / Kommandos", padding=8)
        control_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        control_box.columnconfigure(1, weight=1)

        target_speed_var = tk.StringVar(value=str(int(state.target_speed)))
        target_pos_var = tk.StringVar(value=f"{float(state.target_position_mm):.3f}")
        step_mode_var = tk.StringVar(value=str(int(state.step_mode_to_set)))
        direction_var = tk.StringVar(value=state.direction.value)
        reference_direction_var = tk.StringVar(value=state.reference_direction.value)
        loops_var = tk.StringVar(value=str(int(state.loops)))

        ttk.Label(control_box, text="Target Speed [steps/s]:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(control_box, textvariable=target_speed_var).grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(control_box, text="Target Position [mm]:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(control_box, textvariable=target_pos_var).grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(control_box, text="Step Mode:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(
            control_box,
            textvariable=step_mode_var,
            values=[str(v) for v in NANOTEC_STEP_MODES],
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=2)

        ttk.Label(control_box, text="Direction:").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Combobox(
            control_box,
            textvariable=direction_var,
            values=[MotorDirection.LEFT.value, MotorDirection.RIGHT.value],
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", pady=2)

        ttk.Label(control_box, text="Reference Direction:").grid(row=4, column=0, sticky="w", pady=2)
        ttk.Combobox(
            control_box,
            textvariable=reference_direction_var,
            values=[MotorDirection.LEFT.value, MotorDirection.RIGHT.value],
            state="readonly",
        ).grid(row=4, column=1, sticky="ew", pady=2)

        ttk.Label(control_box, text="Loops:").grid(row=5, column=0, sticky="w", pady=2)
        ttk.Entry(control_box, textvariable=loops_var).grid(row=5, column=1, sticky="ew", pady=2)

        ttk.Button(
            control_box,
            text="Sollwerte uebernehmen",
            command=lambda idx=motor_index: self._apply_motor_settings(idx),
        ).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        ttk.Button(
            control_box,
            text="Sollwerte aus Motor laden",
            command=lambda idx=motor_index: self._load_motor_fields_from_state(idx),
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        cmd_row = ttk.Frame(control_box)
        cmd_row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        cmd_row.columnconfigure(0, weight=1)
        cmd_row.columnconfigure(1, weight=1)
        cmd_row.columnconfigure(2, weight=1)

        ttk.Button(cmd_row, text="Start", command=lambda idx=motor_index: self._start_motor(idx)).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(cmd_row, text="Stop", command=lambda idx=motor_index: self._stop_motor(idx)).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(cmd_row, text="Referenz", command=lambda idx=motor_index: self._reference_motor(idx)).grid(
            row=0, column=2, sticky="ew", padx=(4, 0)
        )
        ttk.Button(
            cmd_row,
            text="Fahrt links",
            command=lambda idx=motor_index: self._jog_motor(idx, MotorDirection.LEFT.value),
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(4, 0))
        ttk.Button(
            cmd_row,
            text="Fahrt rechts",
            command=lambda idx=motor_index: self._jog_motor(idx, MotorDirection.RIGHT.value),
        ).grid(row=1, column=1, sticky="ew", padx=4, pady=(4, 0))
        ttk.Button(
            cmd_row,
            text="Step-Dialog",
            command=lambda idx=motor_index: self._open_step_dialog(idx),
        ).grid(row=1, column=2, sticky="ew", padx=(4, 0), pady=(4, 0))

        preflight_row = ttk.Frame(control_box)
        preflight_row.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        preflight_row.columnconfigure(0, weight=1)
        preflight_row.columnconfigure(1, weight=1)
        ttk.Button(
            preflight_row,
            text="Preflight Start (20s)",
            command=lambda idx=motor_index: self._arm_preflight(idx, "start"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            preflight_row,
            text="Preflight Referenz (20s)",
            command=lambda idx=motor_index: self._arm_preflight(idx, "reference"),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        preflight_start_var = tk.StringVar(value="preflight start: not armed")
        preflight_ref_var = tk.StringVar(value="preflight ref: not armed")
        ttk.Label(control_box, textvariable=preflight_start_var).grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(control_box, textvariable=preflight_ref_var).grid(row=11, column=0, columnspan=2, sticky="w")

        jog_row = ttk.Frame(control_box)
        jog_row.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        jog_row.columnconfigure(0, weight=1)
        jog_row.columnconfigure(1, weight=1)
        ttk.Button(
            jog_row,
            text="Fahrt links",
            command=lambda idx=motor_index: self._jog_motor(idx, MotorDirection.LEFT.value),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            jog_row,
            text="Fahrt rechts",
            command=lambda idx=motor_index: self._jog_motor(idx, MotorDirection.RIGHT.value),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        range_frame = ttk.LabelFrame(control_box, text="Manuell zwischen Schrittzahlen fahren", padding=6)
        range_frame.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        range_frame.columnconfigure(1, weight=1)

        step_a_var = tk.StringVar(value="0")
        step_b_var = tk.StringVar(value="1000")
        range_loops_var = tk.StringVar(value="1")
        ttk.Label(range_frame, text="Schritt A:").grid(row=0, column=0, sticky="w", pady=1)
        ttk.Entry(range_frame, textvariable=step_a_var, width=12).grid(row=0, column=1, sticky="ew", pady=1)
        ttk.Label(range_frame, text="Schritt B:").grid(row=1, column=0, sticky="w", pady=1)
        ttk.Entry(range_frame, textvariable=step_b_var, width=12).grid(row=1, column=1, sticky="ew", pady=1)
        ttk.Label(range_frame, text="Zyklen:").grid(row=2, column=0, sticky="w", pady=1)
        ttk.Entry(range_frame, textvariable=range_loops_var, width=12).grid(row=2, column=1, sticky="ew", pady=1)
        ttk.Button(
            range_frame,
            text="Step-Bereich starten",
            command=lambda idx=motor_index: self._start_step_range(idx),
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(
            range_frame,
            text="Step-Bereich stoppen",
            command=lambda idx=motor_index: self._stop_step_range(idx),
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # ---------------- Live-Status ----------------
        status_box = ttk.LabelFrame(frame, text="Live-Status", padding=8)
        status_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        status_box.columnconfigure(1, weight=1)

        connected_var = tk.StringVar(value="connected: -")
        running_var = tk.StringVar(value="running: -")
        status_code_var = tk.StringVar(value="status code: -")
        status_text_var = tk.StringVar(value="status text: -")
        active_step_mode_var = tk.StringVar(value="active step mode: -")
        actual_pos_var = tk.StringVar(value="actual position: -")
        encoder_pos_var = tk.StringVar(value="encoder position: -")
        actual_steps_var = tk.StringVar(value="actual steps: -")
        relative_steps_var = tk.StringVar(value="steps since zero: -")
        runtime_var = tk.StringVar(value="runtime: -")
        rest_var = tk.StringVar(value="rest time: -")
        expected_var = tk.StringVar(value="expected runtime: -")
        referenced_var = tk.StringVar(value="referenced: -")
        soft_limit_var = tk.StringVar(value="software limits: -")
        limit_reason_var = tk.StringVar(value="last limit stop: -")

        ttk.Label(status_box, textvariable=connected_var).grid(row=0, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=running_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=status_code_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=status_text_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=active_step_mode_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_pos_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=encoder_pos_var).grid(row=6, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=actual_steps_var).grid(row=7, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=relative_steps_var).grid(row=8, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Button(
            status_box,
            text="Nullpunkt = aktueller Schrittwert",
            command=lambda idx=motor_index: self._set_step_zero_here(idx),
        ).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(4, 2))
        ttk.Label(status_box, textvariable=runtime_var).grid(row=10, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=rest_var).grid(row=11, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=expected_var).grid(row=12, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=referenced_var).grid(row=13, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=soft_limit_var).grid(row=14, column=0, columnspan=2, sticky="w", pady=1)
        ttk.Label(status_box, textvariable=limit_reason_var).grid(row=15, column=0, columnspan=2, sticky="w", pady=1)

        ttk.Label(status_box, text="Progress:").grid(row=16, column=0, sticky="w", pady=(8, 2))
        progress = ttk.Progressbar(status_box, orient="horizontal", mode="determinate", maximum=100.0)
        progress.grid(row=16, column=1, sticky="ew", pady=(8, 2))

        motor_leds = ttk.LabelFrame(status_box, text="Motor-LEDs", padding=6)
        motor_leds.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(motor_leds, text="Connected:").grid(row=0, column=0, sticky="w")
        connected_led = self._create_indicator(motor_leds)
        connected_led.grid(row=0, column=1, sticky="w", padx=(4, 12))
        ttk.Label(motor_leds, text="Run:").grid(row=0, column=2, sticky="w")
        running_led = self._create_indicator(motor_leds)
        running_led.grid(row=0, column=3, sticky="w", padx=(4, 0))

        # Tasteranzeige: links/rechts mit kleinen LED-Punkten.
        taster_frame = ttk.LabelFrame(status_box, text="Taster / Endschalter", padding=6)
        taster_frame.grid(row=18, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        taster_frame.columnconfigure(1, weight=1)
        taster_frame.columnconfigure(3, weight=1)

        mapping_text_var = tk.StringVar(value=self._taster_mapping_text(motor_index))
        ttk.Label(
            taster_frame,
            textvariable=mapping_text_var,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        left_text_var = tk.StringVar(value="links: -")
        right_text_var = tk.StringVar(value="rechts: -")

        ttk.Label(taster_frame, text="Links:").grid(row=1, column=0, sticky="w")
        left_led = self._create_indicator(taster_frame)
        left_led.grid(row=1, column=1, sticky="w", padx=(4, 6))
        ttk.Label(taster_frame, textvariable=left_text_var).grid(row=1, column=2, sticky="w")

        ttk.Label(taster_frame, text="Rechts:").grid(row=2, column=0, sticky="w")
        right_led = self._create_indicator(taster_frame)
        right_led.grid(row=2, column=1, sticky="w", padx=(4, 6))
        ttk.Label(taster_frame, textvariable=right_text_var).grid(row=2, column=2, sticky="w")

        self._motor_ui[motor_index] = {
            # Sollwert-Felder
            "target_speed_var": target_speed_var,
            "target_pos_var": target_pos_var,
            "step_mode_var": step_mode_var,
            "direction_var": direction_var,
            "reference_direction_var": reference_direction_var,
            "loops_var": loops_var,
            # Live-Statusfelder
            "connected_var": connected_var,
            "running_var": running_var,
            "status_code_var": status_code_var,
            "status_text_var": status_text_var,
            "active_step_mode_var": active_step_mode_var,
            "actual_pos_var": actual_pos_var,
            "encoder_pos_var": encoder_pos_var,
            "actual_steps_var": actual_steps_var,
            "relative_steps_var": relative_steps_var,
            "runtime_var": runtime_var,
            "rest_var": rest_var,
            "expected_var": expected_var,
            "referenced_var": referenced_var,
            "soft_limit_var": soft_limit_var,
            "limit_reason_var": limit_reason_var,
            "progress": progress,
            "panel_frame": frame,
            "connected_led": connected_led,
            "running_led": running_led,
            # Taster
            "left_led": left_led,
            "right_led": right_led,
            "left_text_var": left_text_var,
            "right_text_var": right_text_var,
            "mapping_text_var": mapping_text_var,
            "preflight_start_var": preflight_start_var,
            "preflight_ref_var": preflight_ref_var,
            "step_a_var": step_a_var,
            "step_b_var": step_b_var,
            "range_loops_var": range_loops_var,
        }

        return frame

    def _taster_mapping_text(self, motor_index: int) -> str:
        if motor_index == 1:
            return "Schleusenkammer: Taster #1=links, #2=rechts, #3=Safety extern"
        return "Sputterkammer: Taster #1=links, #3=rechts, #2=zwischenposition"

    # ------------------------------------------------------------------
    # Runtime bridge
    # ------------------------------------------------------------------
    def _runtime_settings_snapshot(self) -> "RuntimeSettings":
        if self._get_runtime_settings is not None:
            return self._get_runtime_settings()
        return self._controller.get_runtime_settings()

    def _build_runtime_from_form(self) -> "RuntimeSettings":
        runtime = self._runtime_settings_snapshot()
        mode_token = self._mode_select_var.get().strip().lower()
        simulation = mode_token != "real"
        return runtime.with_simulation(simulation)

    def _runtime_with_selected_nanotec_port(self) -> "RuntimeSettings":
        runtime = self._runtime_settings_snapshot()
        ports = dict(runtime.ports)
        ports["nanotec"] = str(self._port_var.get()).strip()
        return type(runtime)(
            simulation=runtime.simulation,
            ports=ports,
            pfeiffer_controller=runtime.pfeiffer_controller,
            pfeiffer_single_gauge=runtime.pfeiffer_single_gauge,
            pfeiffer_maxi_chamber_channel=runtime.pfeiffer_maxi_chamber_channel,
            pfeiffer_maxi_load_channel=runtime.pfeiffer_maxi_load_channel,
        )

    def _mark_address_dirty(self, motor_index: int) -> None:
        if motor_index == 1:
            self._address_m1_dirty = True
        elif motor_index == 2:
            self._address_m2_dirty = True

    def _on_port_selected(self, _event=None) -> None:
        self._port_dirty = True
        self._update_port_controls()

    @staticmethod
    def _nanotec_port_is_connected(port_runtime: object | None) -> bool:
        if port_runtime is None:
            return False
        return bool(getattr(port_runtime, "connected", False))

    def _toggle_service_panel(self) -> None:
        self._set_service_panel_visible(not self._service_panel_visible)

    def _set_service_panel_visible(self, visible: bool) -> None:
        self._service_panel_visible = bool(visible)
        if self._service_panel_visible:
            self._service_panel.grid()
            self._service_toggle_button.configure(text="Service/Test ausblenden")
        else:
            self._service_panel.grid_remove()
            self._service_toggle_button.configure(text="Service/Test anzeigen")

    def _on_service_mode_toggled(self) -> None:
        if self._override_internal_change:
            return
        enable = bool(self._service_mode_var.get())
        if enable:
            confirmed = messagebox.askyesno(
                "Service-Modus bestaetigen",
                (
                    "Service-Modus aktiviert unsichere Test-Overrides.\n\n"
                    "Nur fortfahren, wenn der Fahrweg frei ist und ein sicherer "
                    "Testbetrieb gewaehleistet ist."
                ),
                parent=self,
            )
            if not confirmed:
                self._override_internal_change = True
                self._service_mode_var.set(False)
                self._override_internal_change = False
                return
        try:
            self._controller.set_nanotec_test_override("service_mode", enable)
            self._log("Service-Modus aktiviert." if enable else "Service-Modus deaktiviert.")
        except Exception as exc:
            messagebox.showerror("Service-Modus konnte nicht gesetzt werden", str(exc), parent=self)
            self._log(f"[ERR] Service-Modus setzen fehlgeschlagen: {exc}")
        finally:
            self._sync_override_controls_from_controller()

    def _on_override_toggled(self, key: str) -> None:
        if self._override_internal_change:
            return
        var = self._override_vars.get(key)
        if var is None:
            return
        try:
            self._controller.set_nanotec_test_override(key, bool(var.get()))
            self._log(f"Override gesetzt: {key}={bool(var.get())}")
        except Exception as exc:
            messagebox.showerror("Override konnte nicht gesetzt werden", str(exc), parent=self)
            self._log(f"[ERR] Override setzen fehlgeschlagen ({key}): {exc}")
        finally:
            self._sync_override_controls_from_controller()

    def _reset_overrides_clicked(self) -> None:
        try:
            self._controller.reset_nanotec_test_overrides()
            self._log("Alle Nanotec-Test-Overrides wurden zurueckgesetzt.")
        except Exception as exc:
            messagebox.showerror("Override-Reset fehlgeschlagen", str(exc), parent=self)
            self._log(f"[ERR] Override-Reset fehlgeschlagen: {exc}")
        finally:
            self._sync_override_controls_from_controller()

    def _sync_override_controls_from_controller(self) -> None:
        try:
            state = self._controller.get_nanotec_test_overrides()
        except Exception:
            state = {"service_mode": False}

        self._override_internal_change = True
        try:
            self._service_mode_var.set(bool(state.get("service_mode", False)))
            for key, var in self._override_vars.items():
                var.set(bool(state.get(key, False)))
        finally:
            self._override_internal_change = False

        service_enabled = bool(state.get("service_mode", False))
        for widget in self._override_widgets.values():
            try:
                widget.configure(state=("normal" if service_enabled else "disabled"))
            except Exception:
                pass

        active_non_master = [
            key for key, enabled in state.items() if key != "service_mode" and bool(enabled) and service_enabled
        ]
        if active_non_master:
            self._override_banner_var.set("UNSAFE TEST OVERRIDES AKTIV")
        elif service_enabled:
            self._override_banner_var.set("Service-Modus aktiv (keine Overrides)")
        else:
            self._override_banner_var.set("")

    @staticmethod
    def _format_preflight_line(label: str, report: object) -> str:
        ok = bool(getattr(report, "ok", False))
        unlock_required = bool(getattr(report, "unlock_required", False))
        unlock_active = bool(getattr(report, "unlock_active", False))
        unlock_remaining = float(getattr(report, "unlock_remaining_sec", 0.0))
        reasons = list(getattr(report, "blocking_reasons", ()))

        if not ok:
            reason = reasons[0] if reasons else "unknown"
            gate = "gate=blocked"
            if unlock_required:
                gate = f"gate={'armed' if unlock_active else 'not armed'}"
            return f"{label}: BLOCK ({reason}) | {gate}"

        if unlock_required:
            if unlock_active:
                return f"{label}: OK | gate=armed ({unlock_remaining:.0f}s)"
            return f"{label}: OK | gate=not armed"
        return f"{label}: OK | gate=override/simulation"

    def _update_preflight_status_for_motor(self, motor_index: int, ui: dict[str, object]) -> None:
        try:
            start_report = self._controller.nanotec_preflight("start", motor_index)
            ref_report = self._controller.nanotec_preflight("reference", motor_index)
            self._var(ui, "preflight_start_var").set(self._format_preflight_line("Preflight Start", start_report))
            self._var(ui, "preflight_ref_var").set(self._format_preflight_line("Preflight Ref", ref_report))
        except Exception as exc:
            self._var(ui, "preflight_start_var").set(f"Preflight Start: Fehler ({exc})")
            self._var(ui, "preflight_ref_var").set("Preflight Ref: n/a")

    def _arm_preflight(self, motor_index: int, action: str) -> None:
        try:
            expires = self._controller.arm_nanotec_preflight(action, motor_index, ttl_sec=20)
            remaining = max(0.0, float(expires) - time.monotonic())
            self._log(f"Preflight armed: action={action}, motor={motor_index}, window={remaining:.0f}s")
        except Exception as exc:
            messagebox.showerror("Preflight fehlgeschlagen", f"Motor {motor_index} ({action}): {exc}", parent=self)
            self._log(f"[ERR] Preflight fehlgeschlagen (M{motor_index}, {action}): {exc}")

    def _refresh_ports(self) -> None:
        current = str(self._port_var.get()).strip()
        ports: list[str] = []
        if self._list_serial_ports_cb is not None:
            try:
                ports = list(self._list_serial_ports_cb())
            except Exception as exc:
                self._log(f"[WARN] Portliste konnte nicht gelesen werden: {exc}")
        if current and current not in ports:
            ports.insert(0, current)
        if not ports:
            ports = [""]
        self._port_cb["values"] = ports
        if current in ports:
            self._port_var.set(current)
        else:
            self._port_var.set(ports[0])
        self._update_port_controls()

    def _toggle_port_connection(self) -> None:
        if self._apply_runtime_settings is None:
            messagebox.showerror("Nicht verfuegbar", "Port-Switch ist ohne Runtime-Callback nicht verfuegbar.", parent=self)
            return

        runtime = self._runtime_settings_snapshot()
        selected = str(self._port_var.get()).strip()
        port_runtime = self._controller.state.ports.get("nanotec")
        connected = self._nanotec_port_is_connected(port_runtime)

        try:
            if connected:
                settings = self._runtime_with_selected_nanotec_port()
                settings = type(settings)(
                    simulation=settings.simulation,
                    ports={**settings.ports, "nanotec": ""},
                    pfeiffer_controller=settings.pfeiffer_controller,
                    pfeiffer_single_gauge=settings.pfeiffer_single_gauge,
                    pfeiffer_maxi_chamber_channel=settings.pfeiffer_maxi_chamber_channel,
                    pfeiffer_maxi_load_channel=settings.pfeiffer_maxi_load_channel,
                )
                self._apply_runtime_settings(settings)
                self._port_dirty = False
                self._log("Nanotec-Port getrennt (Runtime neu gestartet).")
                return

            if not selected and not runtime.simulation:
                raise ValueError("Bitte zuerst einen Nanotec-Port auswaehlen.")

            settings = self._runtime_with_selected_nanotec_port()
            self._apply_runtime_settings(settings)
            self._port_dirty = False
            self._log(f"Nanotec-Port gesetzt: '{selected or '<leer>'}'")
        except Exception as exc:
            messagebox.showerror("Nanotec-Portwechsel fehlgeschlagen", str(exc), parent=self)
            self._log(f"[ERR] Nanotec-Portwechsel fehlgeschlagen: {exc}")
        finally:
            self._update_port_controls()

    @staticmethod
    def _normalize_motor_address(text: str) -> str:
        token = str(text).strip()
        if not token.isdigit():
            raise ValueError("Motoradresse muss numerisch sein.")
        value = int(token)
        if not (1 <= value <= 255):
            raise ValueError("Motoradresse muss im Bereich 1..255 liegen.")
        return str(value)

    def _apply_motor_addresses(self) -> None:
        try:
            addr1 = self._normalize_motor_address(self._address_m1_var.get())
            addr2 = self._normalize_motor_address(self._address_m2_var.get())
        except Exception as exc:
            messagebox.showerror("Motoradressen ungueltig", str(exc), parent=self)
            self._log(f"[ERR] Motoradressen ungueltig: {exc}")
            return

        if addr1 == addr2:
            msg = "Motoradresse 1 und 2 duerfen nicht identisch sein."
            messagebox.showerror("Motoradressen ungueltig", msg, parent=self)
            self._log(f"[ERR] {msg}")
            return

        try:
            ok = self._controller.set_motor_addresses(addr1, addr2, reconnect=not self._controller.state.simulation)
            if ok:
                self._address_m1_dirty = False
                self._address_m2_dirty = False
                self._address_m1_var.set(addr1)
                self._address_m2_var.set(addr2)
                self._log(f"Motoradressen gesetzt: M1={addr1}, M2={addr2}")
            else:
                self._log(f"Motoradressen gesetzt, Reconnect meldet Fehler: M1={addr1}, M2={addr2}")
        except Exception as exc:
            messagebox.showerror("Motoradressen konnten nicht gesetzt werden", str(exc), parent=self)
            self._log(f"[ERR] Motoradressen setzen fehlgeschlagen: {exc}")

    def _on_mode_selection_changed(self, _event=None) -> None:
        if self._mode_internal_change:
            return
        self._apply_mode()

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
        except Exception as exc:
            messagebox.showerror("Moduswechsel fehlgeschlagen", str(exc), parent=self)
            self._log(f"Moduswechsel fehlgeschlagen: {exc}")
        finally:
            self._mode_internal_change = True
            try:
                runtime = self._runtime_settings_snapshot()
                self._mode_select_var.set("simulation" if runtime.simulation else "real")
            finally:
                self._mode_internal_change = False
            self._update_mode_indicator()

    # ------------------------------------------------------------------
    # Controller-Aktionen
    # ------------------------------------------------------------------
    def _load_motor_fields_from_state(self, motor_index: int) -> None:
        """
        Schreibt aktuelle Motor-Sollwerte in die Eingabefelder des Panels.

        Nutzen:
        - Bediener koennen nach externen Aenderungen (z. B. Skript, anderer
          GUI-Teil, Reconnect) die Felder mit einem Klick wieder synchronisieren.
        """

        motor = self._controller.state.motor1 if motor_index == 1 else self._controller.state.motor2
        ui = self._motor_ui.get(motor_index)
        if ui is None:
            return

        self._var(ui, "target_speed_var").set(str(int(motor.target_speed)))
        self._var(ui, "target_pos_var").set(f"{float(motor.target_position_mm):.3f}")
        self._var(ui, "step_mode_var").set(str(int(motor.step_mode_to_set)))
        self._var(ui, "direction_var").set(self._ui_direction_for_motor(motor_index, motor.direction.value))
        self._var(ui, "reference_direction_var").set(
            self._ui_direction_for_motor(motor_index, motor.reference_direction.value)
        )
        self._var(ui, "loops_var").set(str(int(motor.loops)))
        self._log(f"Motor {motor_index}: Eingabefelder aus aktuellem State geladen.")

    def _apply_motor_settings(self, motor_index: int) -> None:
        """
        Liest Eingabefelder aus und uebergibt sie an die sichere Controller-API.

        Der Controller uebernimmt:
        - Validierung
        - ggf. Hardware-Apply
        - ggf. Rollback bei Fehlern
        """

        ui = self._motor_ui[motor_index]
        try:
            target_speed = float(self._var(ui, "target_speed_var").get())
            target_position_mm = float(self._var(ui, "target_pos_var").get())
            step_mode = int(self._var(ui, "step_mode_var").get())
            direction = str(self._var(ui, "direction_var").get())
            reference_direction = str(self._var(ui, "reference_direction_var").get())
            loops = int(float(self._var(ui, "loops_var").get()))
            direction = self._effective_direction_for_motor(motor_index, direction)
            reference_direction = self._effective_direction_for_motor(motor_index, reference_direction)

            ok = self._controller.configure_motor(
                motor_index,
                target_speed=target_speed,
                target_position_mm=target_position_mm,
                step_mode=step_mode,
                direction=direction,
                reference_direction=reference_direction,
                loops=loops,
            )
            if ok:
                self._log(f"Motor {motor_index}: Sollwerte wurden uebernommen.")
            else:
                self._log(f"Motor {motor_index}: Sollwerte konnten nicht auf Hardware angewendet werden.")
        except Exception as exc:
            messagebox.showerror(
                "Motor-Sollwerte konnten nicht uebernommen werden",
                f"Motor {motor_index}: {exc}",
                parent=self,
            )
            self._log(f"[ERR] Motor {motor_index} parameter update failed: {exc}")

    def _effective_direction_for_motor(self, motor_index: int, direction: str) -> str:
        """
        Richtungskorrektur fuer die Kammermechanik.

        Motor 2 ist in dieser Anlage invertiert verdrahtet/ausgerichtet:
        UI-Richtung "Left" muss physisch als "Right" gesendet werden (und umgekehrt).
        """

        token = str(direction).strip()
        if motor_index != 2 or not bool(self._invert_m2_direction_var.get()):
            return token
        if token == MotorDirection.LEFT.value:
            return MotorDirection.RIGHT.value
        if token == MotorDirection.RIGHT.value:
            return MotorDirection.LEFT.value
        return token

    def _ui_direction_for_motor(self, motor_index: int, direction: str) -> str:
        return self._effective_direction_for_motor(motor_index, direction)

    def _jog_motor(self, motor_index: int, direction_ui: str) -> None:
        """
        Startet eine manuelle Dauerfahrt nach links/rechts.

        Die Bewegung bleibt ueber die bestehenden Endschalter- und Softlimit-
        Safeties im Controller abgesichert.
        """

        try:
            motor = self._controller.state.motor1 if motor_index == 1 else self._controller.state.motor2
            direction = self._effective_direction_for_motor(motor_index, direction_ui)
            ok = self._controller.configure_motor(
                motor_index,
                target_speed=float(motor.target_speed),
                target_position_mm=99999.0,
                step_mode=int(motor.step_mode_to_set),
                direction=direction,
                reference_direction=self._effective_direction_for_motor(motor_index, motor.reference_direction.value),
                loops=1,
            )
            if not ok:
                self._log(f"Motor {motor_index}: Jog konnte nicht vorbereitet werden.")
                return
            self._controller.start_motor(motor_index)
            self._log(f"Motor {motor_index}: Jog gestartet ({direction_ui}).")
        except Exception as exc:
            messagebox.showerror("Jog fehlgeschlagen", f"Motor {motor_index}: {exc}", parent=self)
            self._log(f"[ERR] Motor {motor_index}: Jog fehlgeschlagen: {exc}")

    def _set_step_zero_here(self, motor_index: int) -> None:
        motor = self._controller.state.motor1 if motor_index == 1 else self._controller.state.motor2
        self._step_zero_offset[motor_index] = int(motor.actual_position_steps)
        self._log(
            f"Motor {motor_index}: Schritt-Nullpunkt gesetzt auf {self._step_zero_offset[motor_index]} (absolut)."
        )

    def _stop_step_range(self, motor_index: int) -> None:
        self._range_running[motor_index] = False
        try:
            self._controller.stop_motor(motor_index)
        except Exception:
            pass
        self._log(f"Motor {motor_index}: Step-Bereich gestoppt.")

    def _start_step_range(self, motor_index: int) -> None:
        if self._range_running.get(motor_index, False):
            self._log(f"Motor {motor_index}: Step-Bereich laeuft bereits.")
            return

        ui = self._motor_ui[motor_index]
        try:
            step_a = int(float(self._var(ui, "step_a_var").get()))
            step_b = int(float(self._var(ui, "step_b_var").get()))
            loops = max(1, int(float(self._var(ui, "range_loops_var").get())))
        except Exception as exc:
            messagebox.showerror("Step-Bereich ungültig", f"Motor {motor_index}: {exc}", parent=self)
            return

        self._range_running[motor_index] = True

        def _worker() -> None:
            try:
                targets = (step_a, step_b)
                for _ in range(loops):
                    if not self._range_running.get(motor_index, False):
                        break
                    for target in targets:
                        if not self._range_running.get(motor_index, False):
                            break
                        self._move_motor_to_relative_step(motor_index, target)
                self._log(f"Motor {motor_index}: Step-Bereich abgeschlossen.")
            except Exception as exc:
                self._log(f"[ERR] Motor {motor_index}: Step-Bereich fehlgeschlagen: {exc}")
            finally:
                self._range_running[motor_index] = False

        threading.Thread(target=_worker, daemon=True).start()

    def _open_step_dialog(self, motor_index: int) -> None:
        ui = self._motor_ui[motor_index]
        win = tk.Toplevel(self)
        win.title(f"Motor {motor_index}: Step-Bereich")
        win.transient(self)
        win.grab_set()

        step_a_var = tk.StringVar(value=self._var(ui, "step_a_var").get())
        step_b_var = tk.StringVar(value=self._var(ui, "step_b_var").get())
        loops_var = tk.StringVar(value=self._var(ui, "range_loops_var").get())

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Schritt A:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(frm, textvariable=step_a_var).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(frm, text="Schritt B:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(frm, textvariable=step_b_var).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(frm, text="Zyklen:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(frm, textvariable=loops_var).grid(row=2, column=1, sticky="ew", pady=2)

        def _apply_and_start() -> None:
            self._var(ui, "step_a_var").set(step_a_var.get())
            self._var(ui, "step_b_var").set(step_b_var.get())
            self._var(ui, "range_loops_var").set(loops_var.get())
            win.destroy()
            self._start_step_range(motor_index)

        ttk.Button(frm, text="Starten", command=_apply_and_start).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _move_motor_to_relative_step(self, motor_index: int, target_relative_step: int) -> None:
        motor = self._controller.state.motor1 if motor_index == 1 else self._controller.state.motor2
        zero = int(self._step_zero_offset.get(motor_index, 0))
        current_abs = int(motor.actual_position_steps)
        target_abs = zero + int(target_relative_step)
        delta_steps = target_abs - current_abs
        if delta_steps == 0:
            return

        target_mm = self._steps_to_mm(abs(delta_steps), motor)
        desired_direction_ui = MotorDirection.LEFT.value if delta_steps > 0 else MotorDirection.RIGHT.value
        desired_direction = self._effective_direction_for_motor(motor_index, desired_direction_ui)

        ok = self._controller.configure_motor(
            motor_index,
            target_speed=float(motor.target_speed),
            target_position_mm=float(target_mm),
            step_mode=int(motor.step_mode_to_set),
            direction=desired_direction,
            reference_direction=self._effective_direction_for_motor(motor_index, motor.reference_direction.value),
            loops=1,
        )
        if not ok:
            raise RuntimeError("configure_motor failed")

        self._controller.start_motor(motor_index)
        self._wait_until_motor_stops(motor_index, timeout_s=180.0)

    @staticmethod
    def _steps_to_mm(steps: int, motor: MotorState) -> float:
        step_mode = int(motor.step_mode_active or motor.step_mode_to_set or 1)
        calibration = float(motor.calibration if abs(motor.calibration) > 1.0e-12 else 1.0)
        return abs(float(steps)) * calibration / (10000.0 * step_mode)

    def _wait_until_motor_stops(self, motor_index: int, timeout_s: float) -> None:
        start = time.monotonic()
        while time.monotonic() - start <= timeout_s:
            motor = self._controller.state.motor1 if motor_index == 1 else self._controller.state.motor2
            if not motor.running:
                return
            if not self._range_running.get(motor_index, True):
                return
            time.sleep(0.05)
        raise TimeoutError(f"Motor {motor_index} motion timeout")

    def _start_motor(self, motor_index: int) -> None:
        try:
            self._controller.start_motor(motor_index)
            self._log(f"Motor {motor_index}: Start ausgeloest (Details siehe Status/Meldungen).")
        except Exception as exc:
            messagebox.showerror("Motorstart fehlgeschlagen", f"Motor {motor_index}: {exc}", parent=self)
            self._log(f"[ERR] Motor {motor_index} start failed: {exc}")

    def _stop_motor(self, motor_index: int) -> None:
        try:
            self._controller.stop_motor(motor_index)
            self._log(f"Motor {motor_index}: Stop angefordert.")
        except Exception as exc:
            messagebox.showerror("Motorstop fehlgeschlagen", f"Motor {motor_index}: {exc}", parent=self)
            self._log(f"[ERR] Motor {motor_index} stop failed: {exc}")

    def _reference_motor(self, motor_index: int) -> None:
        try:
            confirmed = messagebox.askyesno(
                "Referenzfahrt bestaetigen",
                (
                    f"Motor {motor_index}: Referenzfahrt starten?\n\n"
                    "Bitte nur bestaetigen, wenn der Fahrweg frei ist und keine "
                    "mechanische Kollision moeglich ist."
                ),
                parent=self,
            )
            if not confirmed:
                return
            self._controller.reference_motor(motor_index)
            self._log(f"Motor {motor_index}: Referenz ausgeloest (Details siehe Status/Meldungen).")
        except Exception as exc:
            messagebox.showerror("Referenzfahrt fehlgeschlagen", f"Motor {motor_index}: {exc}", parent=self)
            self._log(f"[ERR] Motor {motor_index} reference failed: {exc}")

    def _stop_all_motors(self) -> None:
        confirmed = messagebox.askyesno(
            "STOPP ALLE MOTOREN",
            "Wirklich fuer beide Motoren ein Stop-Kommando senden?",
            parent=self,
        )
        if not confirmed:
            return
        self._controller.stop_all_motors()
        self._log("Globaler Stop fuer beide Motoren angefordert.")

    def _reconnect_nanotec(self) -> None:
        ok = self._controller.reconnect_nanotec()
        if ok:
            self._log("Nanotec-Reconnect erfolgreich.")
        else:
            self._log("Nanotec-Reconnect fehlgeschlagen. Details im Hauptlog.")

    # ------------------------------------------------------------------
    # Tick-Aktualisierung
    # ------------------------------------------------------------------
    def on_state_tick(self, state: PlantState) -> None:
        """
        Wird vom Hauptfenster bei jedem Tick aufgerufen.

        Aufgaben:
        - Backendstatus aktualisieren
        - beide Motorpanels mit Live-Werten fuettern
        """

        runtime_cfg = self._runtime_settings_snapshot()
        mode_token = "simulation" if runtime_cfg.simulation else "real"
        if self._mode_select_var.get() != mode_token:
            self._mode_internal_change = True
            self._mode_select_var.set(mode_token)
            self._mode_internal_change = False
        self._mode_var.set("Simulation" if runtime_cfg.simulation else "Real hardware")
        runtime_port = str(runtime_cfg.ports.get("nanotec", "")).strip()
        if self._port_dirty and str(self._port_var.get()).strip() == runtime_port:
            self._port_dirty = False
        if not self._port_dirty:
            self._port_var.set(runtime_port)

        state_addr1 = str(state.motor1.address)
        if self._address_m1_dirty and str(self._address_m1_var.get()).strip() == state_addr1:
            self._address_m1_dirty = False
        if not self._address_m1_dirty:
            self._address_m1_var.set(state_addr1)

        state_addr2 = str(state.motor2.address)
        if self._address_m2_dirty and str(self._address_m2_var.get()).strip() == state_addr2:
            self._address_m2_dirty = False
        if not self._address_m2_dirty:
            self._address_m2_var.set(state_addr2)
        self._update_mode_indicator()
        self._sync_override_controls_from_controller()

        port_runtime = state.ports.get("nanotec")
        if port_runtime is None:
            self._backend_health_var.set("nanotec backend: unknown")
        else:
            self._backend_health_var.set(
                f"nanotec backend: connected={port_runtime.connected}, failed={port_runtime.failed}, error='{port_runtime.last_error}'"
            )

        self._update_port_controls(port_runtime)

        self._update_motor_panel(1, state.motor1, state)
        self._update_motor_panel(2, state.motor2, state)

    def _update_motor_panel(self, motor_index: int, motor: MotorState, state: PlantState) -> None:
        """
        Schreibt aktuelle Motorwerte in die GUI-Variablen des jeweiligen Panels.
        """

        ui = self._motor_ui.get(motor_index)
        if ui is None:
            return

        panel = ui.get("panel_frame")
        if isinstance(panel, ttk.LabelFrame):
            chamber = self._chamber_labels.get(motor_index, "-")
            panel.configure(text=f"Motor {motor_index} - {chamber} (Adresse {motor.address})")

        self._var(ui, "connected_var").set(f"connected: {bool(motor.connected)}")
        self._var(ui, "running_var").set(f"running: {bool(motor.running)}")
        self._var(ui, "status_code_var").set(f"status code: {int(motor.status_code)}")
        self._var(ui, "status_text_var").set(f"status text: {motor.status_text}")
        self._var(ui, "active_step_mode_var").set(f"active step mode: {int(motor.step_mode_active)}")
        self._var(ui, "actual_pos_var").set(f"actual position: {motor.actual_position_mm:.3f} mm")
        self._var(ui, "encoder_pos_var").set(f"encoder position: {motor.encoder_position_mm:.3f} mm")
        if motor.referenced and not self._reference_zero_latched.get(motor_index, False):
            self._step_zero_offset[motor_index] = int(motor.actual_position_steps)
            self._reference_zero_latched[motor_index] = True
            self._log(
                f"Motor {motor_index}: Referenz erkannt, Schritt-Nullpunkt automatisch auf "
                f"{self._step_zero_offset[motor_index]} gesetzt."
            )
        if not motor.referenced:
            self._reference_zero_latched[motor_index] = False
        abs_steps = int(motor.actual_position_steps)
        rel_steps = abs_steps - int(self._step_zero_offset.get(motor_index, 0))
        encoder_now = float(motor.encoder_position_mm)
        delta_enc = abs(encoder_now - float(self._last_encoder_mm.get(motor_index, 0.0)))
        shaft_moving = bool(motor.running) and delta_enc > 0.001
        self._shaft_moving[motor_index] = shaft_moving
        self._last_encoder_mm[motor_index] = encoder_now
        self._var(ui, "actual_steps_var").set(f"actual steps: {abs_steps}")
        self._var(ui, "relative_steps_var").set(f"steps since zero: {rel_steps}")
        self._var(ui, "runtime_var").set(f"runtime: {motor.runtime_sec:.2f} s")
        self._var(ui, "rest_var").set(f"rest time: {motor.rest_sec:.2f} s")
        self._var(ui, "expected_var").set(f"expected runtime: {motor.expected_runtime_sec:.2f} s")
        self._var(ui, "referenced_var").set(f"referenced: {motor.referenced}")
        self._var(ui, "soft_limit_var").set(self._soft_limit_text_for_motor(motor_index))
        self._var(ui, "limit_reason_var").set(
            f"last limit stop: {motor.limit_stop_reason if motor.limit_stop_reason else '-'}"
        )

        progress_widget = ui.get("progress")
        if isinstance(progress_widget, ttk.Progressbar):
            progress_widget["value"] = motor.progress_percent

        self._set_indicator(ui.get("connected_led"), "#2e7d32" if bool(motor.connected) else "#9e9e9e")
        self._set_indicator(ui.get("running_led"), "#2e7d32" if bool(motor.running) else "#9e9e9e")
        self._cnc_status_vars[motor_index]["motor"].set(
            f"M{motor_index} Motor: {'FAHRT' if bool(motor.running) else 'STOP'}"
        )
        self._cnc_status_vars[motor_index]["shaft"].set(
            f"M{motor_index} Welle: {'DREHT' if shaft_moving else 'STEHT'}"
        )
        self._cnc_status_vars[motor_index]["position"].set(
            f"M{motor_index} Position: {motor.actual_position_mm:.3f} mm | Steps: {rel_steps:+d}"
        )

        # Tasterbits anhand konfigurierbarer Bitnummern lesen.
        if motor_index == 1:
            left_bit = MOTOR1_LEFT_TASTER_BIT
            right_bit = MOTOR1_RIGHT_TASTER_BIT
            left_active_level = MOTOR1_LEFT_TASTER_ACTIVE_LEVEL
            right_active_level = MOTOR1_RIGHT_TASTER_ACTIVE_LEVEL
        else:
            left_bit = MOTOR2_LEFT_TASTER_BIT
            right_bit = MOTOR2_RIGHT_TASTER_BIT
            left_active_level = MOTOR2_LEFT_TASTER_ACTIVE_LEVEL
            right_active_level = MOTOR2_RIGHT_TASTER_ACTIVE_LEVEL

        left_raw = self._expert_e9053_bit(state, left_bit)
        right_raw = self._expert_e9053_bit(state, right_bit)
        left_active = self._bit_is_active(left_raw, left_active_level)
        right_active = self._bit_is_active(right_raw, right_active_level)

        self._set_taster_display(
            ui,
            left_active=left_active,
            right_active=right_active,
            left_raw=left_raw,
            right_raw=right_raw,
            left_bit=left_bit,
            right_bit=right_bit,
        )
        self._update_preflight_status_for_motor(motor_index, ui)

    # ------------------------------------------------------------------
    # Taster-Hilfen
    # ------------------------------------------------------------------
    @staticmethod
    def _expert_e9053_bit(state: PlantState, bit_index: int) -> int | None:
        """
        Liest ein E9053-Bit ueber eine zusammenhaengende Bitnummer.

        Mapping:
        - 0..7   -> e9053_do1[0..7]
        - 8..15  -> e9053_do2[0..7]
        - sonst  -> None
        """

        if bit_index < 0:
            return None
        if 0 <= bit_index <= 7:
            return int(state.expert.e9053_do1[bit_index])
        if 8 <= bit_index <= 15:
            return int(state.expert.e9053_do2[bit_index - 8])
        return None

    @staticmethod
    def _bit_is_active(raw_value: int | None, active_level: int) -> int | None:
        """
        Interpretiert den Rohbitwert gemaess konfiguriertem Aktivpegel.

        Rueckgabe:
        - 1 / 0 bei gueltigem Rohwert
        - None, falls kein gueltiger Rohwert vorhanden ist
        """

        if raw_value is None:
            return None
        return 1 if int(raw_value) == int(active_level) else 0

    def _set_taster_display(
        self,
        ui: dict[str, object],
        *,
        left_active: int | None,
        right_active: int | None,
        left_raw: int | None,
        right_raw: int | None,
        left_bit: int,
        right_bit: int,
    ) -> None:
        """
        Aktualisiert die Taster-LEDs und Texte.
        """

        left_text = self._var(ui, "left_text_var")
        right_text = self._var(ui, "right_text_var")

        left_led = ui.get("left_led")
        right_led = ui.get("right_led")

        if left_active is None:
            left_text.set(f"links: n/a (bit {left_bit})")
            self._set_indicator(left_led, "#9e9e9e")
        else:
            state_text = "AKTIV" if left_active else "INAKTIV"
            left_text.set(f"links: {state_text} (bit {left_bit}, raw={left_raw})")
            self._set_indicator(left_led, "#2e7d32" if left_active else "#c62828")

        if right_active is None:
            right_text.set(f"rechts: n/a (bit {right_bit})")
            self._set_indicator(right_led, "#9e9e9e")
        else:
            state_text = "AKTIV" if right_active else "INAKTIV"
            right_text.set(f"rechts: {state_text} (bit {right_bit}, raw={right_raw})")
            self._set_indicator(right_led, "#2e7d32" if right_active else "#c62828")

    # ------------------------------------------------------------------
    # GUI-Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _var(mapping: dict[str, object], key: str) -> tk.Variable:
        value = mapping.get(key)
        if isinstance(value, tk.Variable):
            return value
        raise RuntimeError(f"Missing Tk variable '{key}'")

    @staticmethod
    def _create_indicator(parent: tk.Misc) -> tk.Canvas:
        canvas = tk.Canvas(parent, width=14, height=14, highlightthickness=0, bd=0)
        oval = canvas.create_oval(2, 2, 12, 12, fill="#9e9e9e", outline="#666666")
        canvas._indicator_oval = oval  # type: ignore[attr-defined]
        return canvas

    @staticmethod
    def _set_indicator(indicator_canvas: object, color: str) -> None:
        if not isinstance(indicator_canvas, tk.Canvas):
            return
        oval = getattr(indicator_canvas, "_indicator_oval", None)
        if oval is not None:
            indicator_canvas.itemconfigure(oval, fill=color)

    def _update_mode_indicator(self) -> None:
        selected = self._mode_select_var.get().strip().lower()
        active = selected
        try:
            runtime = self._runtime_settings_snapshot()
            active = "simulation" if runtime.simulation else "real"
        except Exception:
            pass

        synced = selected == active
        if hasattr(self, "_mode_light"):
            self._set_indicator(self._mode_light, "#2e7d32" if synced else "#f9a825")

        if hasattr(self, "_mode_apply_button"):
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

        self._mode_state_var.set("aktiv" if synced else f"aktiv: {active}")

    def _update_port_controls(self, port_runtime: object | None = None) -> None:
        runtime = self._runtime_settings_snapshot()
        selected = str(self._port_var.get()).strip()
        if selected and hasattr(self, "_port_cb"):
            values = list(self._port_cb.cget("values"))
            if selected not in values:
                values.append(selected)
                self._port_cb["values"] = values

        state = port_runtime
        if state is None:
            state = self._controller.state.ports.get("nanotec")

        connected = False
        ready = True
        failed = False
        if state is not None:
            connected = self._nanotec_port_is_connected(state)
            ready = bool(getattr(state, "ready", True))
            failed = bool(getattr(state, "failed", False))

        if failed:
            self._set_indicator(getattr(self, "_port_ready_light", None), "#c62828")
            self._port_state_var.set("Portstatus: FEHLER")
        elif connected and ready:
            self._set_indicator(getattr(self, "_port_ready_light", None), "#2e7d32")
            self._port_state_var.set("Portstatus: verbunden")
        elif ready:
            self._set_indicator(getattr(self, "_port_ready_light", None), "#9e9e9e")
            self._port_state_var.set("Portstatus: getrennt")
        else:
            self._set_indicator(getattr(self, "_port_ready_light", None), "#f9a825")
            self._port_state_var.set("Portstatus: busy")

        if hasattr(self, "_port_apply_button"):
            if connected:
                self._port_apply_button.configure(text="Port trennen")
            else:
                label = "Port verbinden" if selected else "Port verbinden (Port waehlen)"
                self._port_apply_button.configure(text=label)

    def _soft_limit_summary_text(self) -> str:
        """
        Erzeugt den Header-Text fuer konfigurierte Software-Fahrgrenzen.

        Die Darstellung ist bewusst explizit, damit Bediener nicht raten muessen,
        ob diese Schutzfunktion aktiv ist.
        """

        m1_min, m1_max = self._controller.get_motor_soft_limits(1)
        m2_min, m2_max = self._controller.get_motor_soft_limits(2)
        return (
            "Software-Limits: "
            f"M1[min={self._fmt_soft_limit(m1_min)}, max={self._fmt_soft_limit(m1_max)}], "
            f"M2[min={self._fmt_soft_limit(m2_min)}, max={self._fmt_soft_limit(m2_max)}]"
        )

    def _soft_limit_text_for_motor(self, motor_index: int) -> str:
        """
        Baut eine kurze Live-Zeile fuer die Soft-Limits eines Motors.
        """

        min_mm, max_mm = self._controller.get_motor_soft_limits(motor_index)
        return f"software limits: min={self._fmt_soft_limit(min_mm)}, max={self._fmt_soft_limit(max_mm)}"

    @staticmethod
    def _fmt_soft_limit(value: float | None) -> str:
        if value is None:
            return "disabled"
        return f"{float(value):.3f} mm"

    def _log(self, text: str) -> None:
        self._msg_box.insert("end", text + "\n")
        # Logbox begrenzen, damit die GUI bei Langzeitbetrieb nicht traege wird.
        max_lines = 1200
        current_lines = int(float(self._msg_box.index("end-1c").split(".")[0]))
        if current_lines > max_lines:
            self._msg_box.delete("1.0", f"{current_lines - max_lines}.0")
        self._msg_box.see("end")

    def set_controller(self, controller: "Controller") -> None:
        self._controller = controller
        self._port_dirty = False
        self._address_m1_dirty = False
        self._address_m2_dirty = False
        runtime = self._runtime_settings_snapshot()
        self._mode_internal_change = True
        self._mode_select_var.set("simulation" if runtime.simulation else "real")
        self._mode_internal_change = False
        self._mode_var.set("Simulation" if runtime.simulation else "Real hardware")
        self._port_var.set(str(runtime.ports.get("nanotec", "")).strip())
        self._address_m1_var.set(str(self._controller.state.motor1.address))
        self._address_m2_var.set(str(self._controller.state.motor2.address))
        self._backend_health_var.set("nanotec backend: unknown")
        self._refresh_ports()
        self._sync_override_controls_from_controller()
        self._update_mode_indicator()
        self._update_port_controls()
        self._load_motor_fields_from_state(1)
        self._load_motor_fields_from_state(2)
        self._log("Controller-Handle nach Runtime-Wechsel aktualisiert.")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        try:
            self._controller.reset_nanotec_test_overrides()
        except Exception:
            pass
        self.destroy()

    def close_window(self) -> None:
        self._on_close()


__all__ = ["NanotecWindow"]
