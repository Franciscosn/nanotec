from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from sputtering_app.controller import Controller
from sputtering_app.devices.transport import list_serial_ports
from sputtering_app.nanotec_gui import NanotecWindow
from sputtering_app.runtime_settings import RuntimeSettings, default_runtime_settings, find_default_settings_file, load_runtime_settings

DIAG_LOG = Path(__file__).resolve().parent / "nanotec_standalone_diagnostics.log"


class NanotecStandaloneRuntime:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Nanotec Standalone Host")
        self.window: NanotecWindow | None = None
        self._port_error_notified = False

        self.runtime_settings = self._load_initial_runtime()
        self.controller = Controller(on_message=self._on_message, runtime=self.runtime_settings)
        self._apply_standalone_defaults(self.controller)

        # Gewuenschte Darstellung laut Bedienwunsch:
        # links: Motor 2 (Sputterkammer), rechts: Motor 1 (Schleusenkammer)
        self.window = NanotecWindow(
            self.root,
            self.controller,
            motor_order=(2, 1),
            chamber_labels={1: "Schleusenkammer", 2: "Sputterkammer"},
            get_runtime_settings=self._get_runtime_settings,
            apply_runtime_settings=self._apply_runtime_settings,
            list_serial_ports_cb=list_serial_ports,
        )
        self.window.title("Schrittmotoren (Nanotec) - Standalone")
        self.window.protocol("WM_DELETE_WINDOW", self._shutdown)

        self._tick_ms = 200
        self._alive = True
        self._tick_inflight = False
        self._tick_lock = threading.Lock()
        self._nanotec_backoff_until = 0.0
        self._diag("standalone started")
        self._schedule_tick()

    def _load_initial_runtime(self) -> RuntimeSettings:
        settings = default_runtime_settings()
        settings_file = find_default_settings_file(".")
        if settings_file is None:
            return settings
        try:
            settings = load_runtime_settings(settings_file, base=settings)
        except Exception:
            pass
        # Standalone: nur Nanotec aktiv lassen, andere Backends deaktivieren
        # damit die GUI nicht durch Timeouts fremder Geraete blockiert.
        ports = dict(settings.ports)
        for key in ("dualg", "fug", "pinnacle", "expert"):
            ports[key] = ""
        return RuntimeSettings(
            simulation=settings.simulation,
            ports=ports,
            pfeiffer_controller=settings.pfeiffer_controller,
            pfeiffer_single_gauge=settings.pfeiffer_single_gauge,
            pfeiffer_maxi_chamber_channel=settings.pfeiffer_maxi_chamber_channel,
            pfeiffer_maxi_load_channel=settings.pfeiffer_maxi_load_channel,
        )

    def _on_message(self, message: str) -> None:
        msg_l = str(message).lower()
        if ("permissionerror(13" in msg_l or "could not open port" in msg_l) and "nanotec" in msg_l:
            self._nanotec_backoff_until = time.monotonic() + 2.5
            if not self._port_error_notified:
                self._port_error_notified = True
                self.root.after(
                    0,
                    lambda: messagebox.showwarning(
                        "COM-Port blockiert",
                        (
                            "COM-Port kann nicht geoeffnet werden (Zugriff verweigert).\n\n"
                            "Bitte andere Programme mit COM4 schliessen (z.B. NanoPro/Terminal) "
                            "und dann 'Nanotec neu verbinden' klicken."
                        ),
                        parent=self.window if self.window is not None else self.root,
                    ),
                )

        # Standalone-Log auf relevante Meldungen begrenzen.
        if "nanotec" not in msg_l and "motor " not in msg_l and "preflight" not in msg_l:
            return

        self._diag(f"controller message: {message}")
        if self.window is not None and self.window.winfo_exists():
            try:
                self.window._log(message)  # noqa: SLF001 - reuse existing message pane
            except Exception:
                pass

    def _get_runtime_settings(self) -> RuntimeSettings:
        return self.runtime_settings

    def _apply_runtime_settings(self, runtime: RuntimeSettings) -> None:
        ports = dict(runtime.ports)
        for key in ("dualg", "fug", "pinnacle", "expert"):
            ports[key] = ""
        self.runtime_settings = RuntimeSettings(
            simulation=runtime.simulation,
            ports=ports,
            pfeiffer_controller=runtime.pfeiffer_controller,
            pfeiffer_single_gauge=runtime.pfeiffer_single_gauge,
            pfeiffer_maxi_chamber_channel=runtime.pfeiffer_maxi_chamber_channel,
            pfeiffer_maxi_load_channel=runtime.pfeiffer_maxi_load_channel,
        )
        old_controller = self.controller
        self.controller = Controller(on_message=self._on_message, runtime=self.runtime_settings)
        self._apply_standalone_defaults(self.controller)
        self.window.set_controller(self.controller)
        try:
            old_controller.shutdown()
        except Exception:
            pass

    def _schedule_tick(self) -> None:
        if not self._alive:
            return
        if not self._tick_inflight:
            self._tick_inflight = True
            threading.Thread(target=self._tick_worker, daemon=True).start()
        self.root.after(self._tick_ms, self._schedule_tick)

    def _tick_worker(self) -> None:
        t0 = time.monotonic()
        try:
            now = time.monotonic()
            if now < self._nanotec_backoff_until:
                self.root.after(0, lambda: self.window.on_state_tick(self.controller.state))
                return
            with self._tick_lock:
                # Standalone soll nur Nanotec bedienen (keine anderen Backends ticken).
                self.controller._tick_nanotec_port(time.monotonic())  # noqa: SLF001
                state = self.controller.state
            elapsed = time.monotonic() - t0
            if elapsed > 0.35:
                self._diag(f"slow tick: {elapsed:.3f}s")
            self.root.after(0, lambda: self.window.on_state_tick(state))
        except Exception as exc:
            self._on_message(f"[ERR] standalone tick: {exc}")
            self._diag(f"tick error: {exc}")
        finally:
            self._tick_inflight = False

    def _shutdown(self) -> None:
        self._alive = False
        self._diag("standalone shutdown requested")
        try:
            self.controller.shutdown()
        except Exception:
            pass
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

    @staticmethod
    def _apply_standalone_defaults(controller: Controller) -> None:
        """
        Fuer Standalone-Betrieb:
        - Preflight-Huerde deaktivieren (Legacy-artiger Start links/rechts)
        - unbekannte Limit-Eingaenge tolerieren
        - kuerzeres Nanotec-Timeout fuer schnellere Port-Reaktionen
        """

        try:
            controller.set_nanotec_test_override("service_mode", True)
            controller.set_nanotec_test_override("bypass_preflight_requirement", True)
            controller.set_nanotec_test_override("allow_unknown_limit_inputs", True)
        except Exception:
            pass
        try:
            s = controller.nanotec_device.settings
            controller.nanotec_device.settings = type(s)(
                port=s.port,
                baudrate=s.baudrate,
                parity=s.parity,
                bytesize=s.bytesize,
                stopbits=s.stopbits,
                timeout=0.25,
            )
        except Exception:
            pass

    @staticmethod
    def _diag(msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with DIAG_LOG.open("a", encoding="utf-8") as fh:
                fh.write(f"{ts}; {msg}\n")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        NanotecStandaloneRuntime().run()
    except Exception as exc:
        # Fallback fuer Startfehler ausserhalb der Haupt-GUI.
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Nanotec Standalone Startfehler", str(exc), parent=root)
        root.destroy()
        raise
