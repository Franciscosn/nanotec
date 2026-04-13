from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from sputtering_app.devices.nanotec import NanotecDevice
from sputtering_app.devices.transport import SerialDeviceTransport, SerialSettings, list_serial_ports
from sputtering_app.models import MotorDirection, MotorState


LOG_PATH = Path(__file__).resolve().parent / "nanotec_motor_events.log"


@dataclass
class ManualInputs:
    t1: bool = False
    t2: bool = False
    t3: bool = False


class NanotecOnlyApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Nanotec Schrittmotor-Steuerung (Standalone)")
        self.root.geometry("1260x880")

        self.transport = SerialDeviceTransport()
        self.settings = SerialSettings(port="", baudrate=115200, parity="N", bytesize=8, stopbits=1, timeout=0.5)
        self.device = NanotecDevice(self.transport, self.settings)

        self.motor1 = MotorState(address="1", target_speed=600, target_position_mm=600.0)
        self.motor2 = MotorState(address="2", target_speed=600, target_position_mm=200.0)

        self.motor1_inputs = ManualInputs()
        self.motor2_inputs = ManualInputs()
        self.shutter_open = tk.BooleanVar(value=False)
        self.connected = tk.BooleanVar(value=False)
        self.busy = False
        self._stop_requested = False

        self._last_encoder_m1 = 0.0
        self._last_encoder_m2 = 0.0
        self._last_encoder_ts = time.monotonic()

        self.port_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Nicht verbunden")
        self.encoder_lamp_m1 = tk.StringVar(value="⚫")
        self.encoder_lamp_m2 = tk.StringVar(value="⚫")
        self.encoder_text_m1 = tk.StringVar(value="M1 Welle: unbekannt")
        self.encoder_text_m2 = tk.StringVar(value="M2 Welle: unbekannt")

        self._build_ui()
        self._refresh_ports()
        self._schedule_poll()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        top = ttk.LabelFrame(outer, text="Verbindung", padding=8)
        top.pack(fill="x", pady=(0, 10))

        ttk.Button(top, text="Ports aktualisieren", command=self._refresh_ports).grid(row=0, column=0, padx=(0, 6), pady=4)
        self.port_cb = ttk.Combobox(top, textvariable=self.port_var, state="readonly", width=18)
        self.port_cb.grid(row=0, column=1, padx=6, pady=4)
        ttk.Button(top, text="Verbinden", command=self._connect).grid(row=0, column=2, padx=6, pady=4)
        ttk.Button(top, text="STOP", command=self._manual_stop_all).grid(row=0, column=3, padx=10, pady=4)

        ttk.Checkbutton(top, text="Shutter OPEN", variable=self.shutter_open).grid(row=0, column=4, padx=(16, 4), pady=4)
        ttk.Label(top, text="(Vor Fahrtzustand manuell aktualisieren)").grid(row=0, column=5, sticky="w")

        ttk.Label(top, textvariable=self.status_var).grid(row=1, column=0, columnspan=6, sticky="w")

        panes = ttk.Frame(outer)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)

        self._build_motor_panel(panes, 1).grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_motor_panel(panes, 2).grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        seq = ttk.LabelFrame(outer, text="Prozessfunktionen", padding=8)
        seq.pack(fill="x", pady=(10, 0))

        ttk.Button(seq, text="Einschleusung (Motor 1 bis Taster 2)", command=self._start_einschleusung_m1).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(seq, text="Ausschleusung (Motor 1 bis Taster 1)", command=self._start_ausschleusung_m1).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(seq, text="Referenzfahrt M1 (bis Taster 1)", command=self._reference_m1).grid(row=0, column=2, padx=4, pady=4)

        ttk.Button(seq, text="Referenzfahrt M2 (rechts bis Taster 3)", command=self._reference_m2).grid(row=1, column=0, padx=4, pady=4)
        ttk.Button(seq, text="Transfer in Sputterkammer (M2 links bis Taster 2)", command=self._transfer_m2).grid(row=1, column=1, padx=4, pady=4)
        ttk.Button(seq, text="Sputterprozess starten", command=self._start_sputter_dialog).grid(row=1, column=2, padx=4, pady=4)

    def _build_motor_panel(self, parent: ttk.Frame, idx: int) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=f"Motor {idx}", padding=8)
        frame.columnconfigure(1, weight=1)

        inputs = self.motor1_inputs if idx == 1 else self.motor2_inputs
        ttk.Checkbutton(frame, text="Taster 1 aktiv", command=lambda: self._set_input(idx, 1), variable=tk.BooleanVar(value=False)).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Taster 2 aktiv", command=lambda: self._set_input(idx, 2), variable=tk.BooleanVar(value=False)).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Taster 3 aktiv", command=lambda: self._set_input(idx, 3), variable=tk.BooleanVar(value=False)).grid(row=2, column=0, sticky="w")

        pos_var = tk.StringVar(value="Position: -")
        run_var = tk.StringVar(value="Status: -")
        enc_var = self.encoder_text_m1 if idx == 1 else self.encoder_text_m2
        lamp_var = self.encoder_lamp_m1 if idx == 1 else self.encoder_lamp_m2

        ttk.Label(frame, textvariable=pos_var).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(frame, textvariable=run_var).grid(row=4, column=0, sticky="w")
        ttk.Label(frame, textvariable=enc_var).grid(row=5, column=0, sticky="w")
        ttk.Label(frame, textvariable=lamp_var, font=("Arial", 20)).grid(row=5, column=1, sticky="e")

        if idx == 1:
            self.m1_pos_var, self.m1_run_var = pos_var, run_var
        else:
            self.m2_pos_var, self.m2_run_var = pos_var, run_var

        return frame

    def _set_input(self, idx: int, t: int) -> None:
        # Click-to-toggle nur softwareseitig, als Backup/Simulation der Taster.
        target = self.motor1_inputs if idx == 1 else self.motor2_inputs
        if t == 1:
            target.t1 = not target.t1
        elif t == 2:
            target.t2 = not target.t2
        else:
            target.t3 = not target.t3

    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_cb["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Port", "Bitte COM-Port auswaehlen")
            return
        self.settings = SerialSettings(port=port, baudrate=115200, parity="N", bytesize=8, stopbits=1, timeout=0.5)
        self.device = NanotecDevice(self.transport, self.settings)
        try:
            ok1 = self.device.check_motor(self.motor1.address)
            ok2 = self.device.check_motor(self.motor2.address)
        except Exception as exc:
            self.connected.set(False)
            self.status_var.set(f"Verbindungsfehler: {exc}")
            return

        self.connected.set(ok1 or ok2)
        self.status_var.set(f"Verbunden={self.connected.get()} (M1={ok1}, M2={ok2})")
        self._log_event("CONNECT", f"port={port}; m1={ok1}; m2={ok2}")

    def _schedule_poll(self) -> None:
        self._poll_once()
        self.root.after(300, self._schedule_poll)

    def _poll_once(self) -> None:
        if not self.connected.get():
            return
        try:
            self.device.poll_motor(self.motor1)
            self.device.poll_motor(self.motor2)
        except Exception as exc:
            self.status_var.set(f"Polling-Fehler: {exc}")
            return

        self.m1_pos_var.set(f"Position: {self.motor1.actual_position_mm:.2f} mm")
        self.m2_pos_var.set(f"Position: {self.motor2.actual_position_mm:.2f} mm")
        self.m1_run_var.set(f"Status: {self.motor1.status_text} ({self.motor1.status_code})")
        self.m2_run_var.set(f"Status: {self.motor2.status_text} ({self.motor2.status_code})")

        now = time.monotonic()
        if now - self._last_encoder_ts >= 0.6:
            delta1 = abs(self.motor1.encoder_position_mm - self._last_encoder_m1)
            delta2 = abs(self.motor2.encoder_position_mm - self._last_encoder_m2)
            self._update_encoder_lamp(1, delta1 > 0.01 and self.motor1.running)
            self._update_encoder_lamp(2, delta2 > 0.01 and self.motor2.running)
            self._last_encoder_m1 = self.motor1.encoder_position_mm
            self._last_encoder_m2 = self.motor2.encoder_position_mm
            self._last_encoder_ts = now

    def _update_encoder_lamp(self, idx: int, rotating: bool) -> None:
        lamp = "🟢" if rotating else "🔴"
        text = "dreht" if rotating else "steht"
        if idx == 1:
            self.encoder_lamp_m1.set(lamp)
            self.encoder_text_m1.set(f"M1 Welle: {text}")
        else:
            self.encoder_lamp_m2.set(lamp)
            self.encoder_text_m2.set(f"M2 Welle: {text}")

    def _require_ready(self) -> bool:
        if not self.connected.get():
            messagebox.showwarning("Nicht verbunden", "Bitte zuerst verbinden")
            return False
        if self.busy:
            messagebox.showwarning("Busy", "Es laeuft bereits ein Ablauf")
            return False
        return True

    def _manual_stop_all(self) -> None:
        self._stop_requested = True
        try:
            self.device.stop_profile(self.motor1)
            self.device.stop_profile(self.motor2)
        except Exception:
            pass
        self._log_event("STOP", "manual stop all")

    def _run_background(self, name: str, worker) -> None:
        if not self._require_ready():
            return
        self.busy = True
        self._stop_requested = False
        self.status_var.set(f"Ablauf gestartet: {name}")
        self._log_event("START", name)

        def _job() -> None:
            try:
                worker()
                self.root.after(0, lambda: self.status_var.set(f"Ablauf beendet: {name}"))
                self._log_event("END", name)
            except Exception as exc:
                self.root.after(0, lambda: self.status_var.set(f"Fehler ({name}): {exc}"))
                self._log_event("ERROR", f"{name}: {exc}")
            finally:
                self.busy = False

        threading.Thread(target=_job, daemon=True).start()

    def _start_einschleusung_m1(self) -> None:
        if not self.shutter_open.get():
            messagebox.showwarning("Shutter", "Shutter ist CLOSED. Einschleusung gesperrt.")
            return

        def _worker() -> None:
            self._move_until_inputs(self.motor1, MotorDirection.RIGHT, stop_if=lambda: self.motor1_inputs.t2 or self.motor1_inputs.t3, reason="M1 T2/T3")

        self._run_background("Einschleusung M1", _worker)

    def _start_ausschleusung_m1(self) -> None:
        def _worker() -> None:
            self._move_until_inputs(self.motor1, MotorDirection.LEFT, stop_if=lambda: self.motor1_inputs.t1, reason="M1 T1")

        self._run_background("Ausschleusung M1", _worker)

    def _reference_m1(self) -> None:
        def _worker() -> None:
            self._move_until_inputs(self.motor1, MotorDirection.LEFT, stop_if=lambda: self.motor1_inputs.t1, reason="M1 Referenz T1", set_reference=True)

        self._run_background("Referenz M1", _worker)

    def _reference_m2(self) -> None:
        def _worker() -> None:
            self._move_until_inputs(self.motor2, MotorDirection.RIGHT, stop_if=lambda: self.motor2_inputs.t3, reason="M2 Referenz T3", set_reference=True)

        self._run_background("Referenz M2", _worker)

    def _transfer_m2(self) -> None:
        def _worker() -> None:
            if not self.shutter_open.get() and self.motor2_inputs.t1:
                raise RuntimeError("Shutter ist geschlossen und M2 steht an Taster 1: ueberfahren verboten")
            if self.shutter_open.get():
                cond = lambda: self.motor2_inputs.t2 or self.motor2_inputs.t3
                reason = "M2 bis T2/T3 (Shutter open)"
            else:
                cond = lambda: self.motor2_inputs.t1 or self.motor2_inputs.t3
                reason = "M2 bis T1/T3 (Shutter closed)"
            self._move_until_inputs(self.motor2, MotorDirection.LEFT, stop_if=cond, reason=reason)

        self._run_background("Transfer M2", _worker)

    def _start_sputter_dialog(self) -> None:
        runs = simpledialog.askinteger("Sputter-Runs", "Anzahl Runs (1..500):", minvalue=1, maxvalue=500)
        if not runs:
            return

        def _worker() -> None:
            for i in range(runs):
                if self._stop_requested:
                    break
                self._move_until_inputs(self.motor2, MotorDirection.RIGHT, stop_if=lambda: self.motor2_inputs.t3, reason=f"Run {i+1}: bis T3")
                if self._stop_requested:
                    break
                self._move_until_inputs(
                    self.motor2,
                    MotorDirection.LEFT,
                    stop_if=(lambda: self.motor2_inputs.t2) if self.shutter_open.get() else (lambda: self.motor2_inputs.t1),
                    reason=f"Run {i+1}: Rueckweg",
                )

        self._run_background(f"Sputterprozess x{runs}", _worker)

    def _move_until_inputs(self, motor: MotorState, direction: MotorDirection, stop_if, reason: str, set_reference: bool = False) -> None:
        motor.direction = direction
        motor.target_speed = 700
        motor.target_position_mm = 50000.0
        motor.loops = 1
        self.device.start_profile(motor)

        self._log_event("MOVE", f"motor={motor.address}; dir={direction.value}; reason={reason}")
        while True:
            if self._stop_requested:
                self.device.stop_profile(motor)
                self._log_event("STOP", f"motor={motor.address}; manual stop")
                break

            self.device.poll_motor(motor)
            if stop_if():
                self.device.stop_profile(motor)
                if set_reference:
                    self.device._write(motor.address, "D0")
                self._log_event("LIMIT", f"motor={motor.address}; {reason}; pos_mm={motor.actual_position_mm:.3f}")
                break

            if not motor.running:
                self._log_event("MOTOR_STOP", f"motor={motor.address}; status={motor.status_code}")
                break

            self._log_event("STEP", f"motor={motor.address}; pos_mm={motor.actual_position_mm:.3f}; enc_mm={motor.encoder_position_mm:.3f}")
            time.sleep(0.2)

    @staticmethod
    def _log_event(kind: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts}; {kind}; {message}\n")


if __name__ == "__main__":
    root = tk.Tk()
    app = NanotecOnlyApp(root)
    root.mainloop()
