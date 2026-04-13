from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from sputtering_app.devices.nanotec import NanotecDevice
from sputtering_app.devices.transport import SerialDeviceTransport, SerialSettings, list_serial_ports
from sputtering_app.models import MotorDirection, MotorState

DIAG_LOG = Path(__file__).resolve().parent / "nanotec_standalone_diagnostics.log"


class SimpleNanotecApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Nanotec Standalone (Simple)")
        self.root.geometry("1100x760")

        self.transport = SerialDeviceTransport()
        self.settings = SerialSettings(port="", baudrate=115200, parity="N", bytesize=8, stopbits=1, timeout=0.25)
        self.device = NanotecDevice(self.transport, self.settings)
        self.serial_lock = threading.Lock()

        self.motor1 = MotorState(address="1", target_speed=900, target_position_mm=120.0)
        self.motor2 = MotorState(address="2", target_speed=900, target_position_mm=120.0)
        self._connected = False

        self.port_var = tk.StringVar(value="")
        self.global_status_var = tk.StringVar(value="Nicht verbunden")

        self.ui = {
            1: {
                "addr": tk.StringVar(value="1"),
                "speed": tk.StringVar(value="900"),
                "dist": tk.StringVar(value="120"),
                "step": tk.StringVar(value="2"),
                "status": tk.StringVar(value="M1: idle"),
                "pos": tk.StringVar(value="Pos: -"),
                "enc": tk.StringVar(value="Encoder: -"),
            },
            2: {
                "addr": tk.StringVar(value="2"),
                "speed": tk.StringVar(value="900"),
                "dist": tk.StringVar(value="120"),
                "step": tk.StringVar(value="2"),
                "status": tk.StringVar(value="M2: idle"),
                "pos": tk.StringVar(value="Pos: -"),
                "enc": tk.StringVar(value="Encoder: -"),
            },
        }

        self._build_ui()
        self._refresh_ports()
        self._schedule_poll()

    def _build_ui(self) -> None:
        top = ttk.LabelFrame(self.root, text="Verbindung", padding=8)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Button(top, text="Ports aktualisieren", command=self._refresh_ports).grid(row=0, column=0, padx=4)
        self.port_cb = ttk.Combobox(top, textvariable=self.port_var, state="readonly", width=14)
        self.port_cb.grid(row=0, column=1, padx=4)
        ttk.Button(top, text="Port verbinden", command=self._connect).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Port testen", command=self._check_port).grid(row=0, column=3, padx=4)
        ttk.Label(top, textvariable=self.global_status_var).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        self._build_motor_panel(body, 1, "Schleusenkammer (Motor 1)").grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._build_motor_panel(body, 2, "Sputterkammer (Motor 2)").grid(row=0, column=0, sticky="nsew", padx=(0, 6))

    def _build_motor_panel(self, parent: tk.Misc, idx: int, title: str) -> ttk.LabelFrame:
        f = ttk.LabelFrame(parent, text=title, padding=10)
        v = self.ui[idx]

        ttk.Label(f, text="Adresse").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=v["addr"], width=8).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Speed [steps/s]").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=v["speed"], width=10).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Weg [mm]").grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=v["dist"], width=10).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Step mode").grid(row=3, column=0, sticky="w")
        ttk.Entry(f, textvariable=v["step"], width=10).grid(row=3, column=1, sticky="w", pady=2)

        btn = ttk.Frame(f)
        btn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for i in range(4):
            btn.columnconfigure(i, weight=1)
        ttk.Button(btn, text="Fahrt links", command=lambda m=idx: self._start_move(m, MotorDirection.LEFT)).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn, text="Fahrt rechts", command=lambda m=idx: self._start_move(m, MotorDirection.RIGHT)).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn, text="Referenz", command=lambda m=idx: self._reference(m)).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(btn, text="Stop", command=lambda m=idx: self._stop(m)).grid(row=0, column=3, sticky="ew", padx=2)

        ttk.Label(f, textvariable=v["status"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Label(f, textvariable=v["pos"]).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(f, textvariable=v["enc"]).grid(row=7, column=0, columnspan=2, sticky="w")
        return f

    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_cb["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Port", "Bitte COM-Port auswählen.")
            return
        self.settings = SerialSettings(port=port, baudrate=115200, parity="N", bytesize=8, stopbits=1, timeout=0.25)
        self.device = NanotecDevice(self.transport, self.settings)
        self._connected = True
        self.global_status_var.set(f"Port gesetzt: {port}")
        self._diag(f"connect: port={port}")

    def _check_port(self) -> None:
        if not self._connected:
            self.global_status_var.set("Bitte zuerst Port verbinden.")
            return

        def _worker() -> None:
            with self.serial_lock:
                try:
                    ok1 = self.device.check_motor(self.ui[1]["addr"].get().strip())
                    ok2 = self.device.check_motor(self.ui[2]["addr"].get().strip())
                    self.root.after(0, lambda: self.global_status_var.set(f"Porttest: M1={ok1}, M2={ok2}"))
                except Exception as exc:
                    self._handle_serial_error(exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _motor_obj(self, idx: int) -> MotorState:
        return self.motor1 if idx == 1 else self.motor2

    def _apply_ui_to_motor(self, idx: int, direction: MotorDirection | None = None) -> MotorState:
        m = self._motor_obj(idx)
        v = self.ui[idx]
        m.address = v["addr"].get().strip() or m.address
        m.target_speed = int(float(v["speed"].get()))
        m.target_position_mm = float(v["dist"].get())
        m.step_mode_to_set = int(float(v["step"].get()))
        if direction is not None:
            m.direction = direction
        return m

    def _start_move(self, idx: int, direction: MotorDirection) -> None:
        if not self._connected:
            self.global_status_var.set("Nicht verbunden. Bitte zuerst Port verbinden.")
            return

        def _worker() -> None:
            with self.serial_lock:
                try:
                    m = self._apply_ui_to_motor(idx, direction)
                    self.device.start_profile(m)
                    self.root.after(0, lambda: self.ui[idx]["status"].set(f"M{idx}: Fahrt gestartet ({direction.value})"))
                    self._diag(f"move start: m{idx} dir={direction.value}")
                except Exception as exc:
                    self._handle_serial_error(exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _reference(self, idx: int) -> None:
        if not self._connected:
            self.global_status_var.set("Nicht verbunden. Bitte zuerst Port verbinden.")
            return

        def _worker() -> None:
            with self.serial_lock:
                try:
                    m = self._apply_ui_to_motor(idx)
                    self.device.start_reference(m)
                    self.root.after(0, lambda: self.ui[idx]["status"].set(f"M{idx}: Referenz gestartet"))
                    self._diag(f"reference start: m{idx}")
                except Exception as exc:
                    self._handle_serial_error(exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _stop(self, idx: int) -> None:
        if not self._connected:
            return

        def _worker() -> None:
            with self.serial_lock:
                try:
                    self.device.stop_profile(self._motor_obj(idx))
                    self.root.after(0, lambda: self.ui[idx]["status"].set(f"M{idx}: Stop"))
                except Exception as exc:
                    self._handle_serial_error(exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _schedule_poll(self) -> None:
        self._poll_once()
        self.root.after(300, self._schedule_poll)

    def _poll_once(self) -> None:
        if not self._connected:
            return

        def _worker() -> None:
            with self.serial_lock:
                try:
                    for idx, m in ((1, self.motor1), (2, self.motor2)):
                        self.device.poll_motor(m)
                        self.root.after(0, lambda i=idx, mm=m.actual_position_mm, em=m.encoder_position_mm, st=m.status_text: self._update_live(i, mm, em, st))
                except Exception as exc:
                    self._handle_serial_error(exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _update_live(self, idx: int, pos_mm: float, enc_mm: float, status: str) -> None:
        self.ui[idx]["pos"].set(f"Pos: {pos_mm:.3f} mm")
        self.ui[idx]["enc"].set(f"Encoder: {enc_mm:.3f} mm")
        self.ui[idx]["status"].set(f"M{idx}: {status}")

    def _handle_serial_error(self, exc: Exception) -> None:
        text = str(exc)
        self._diag(f"serial error: {text}")
        if "PermissionError(13" in text or "could not open port" in text:
            msg = (
                "COM-Port blockiert: Zugriff verweigert.\n"
                "Bitte NanoPro/Terminal schließen, USB ggf. neu stecken, danach erneut verbinden."
            )
        elif "simulation mode" in text.lower():
            msg = "Serial ist in Simulation deaktiviert. Bitte realen Port verbinden."
        else:
            msg = f"Serieller Fehler: {text}"
        self.root.after(0, lambda: self.global_status_var.set(msg))

    @staticmethod
    def _diag(msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with DIAG_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{ts}; {msg}\n")


if __name__ == "__main__":
    r = tk.Tk()
    app = SimpleNanotecApp(r)
    r.mainloop()
