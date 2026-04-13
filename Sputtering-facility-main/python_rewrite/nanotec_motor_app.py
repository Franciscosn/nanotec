from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from sputtering_app.controller import Controller
from sputtering_app.devices.transport import list_serial_ports
from sputtering_app.nanotec_gui import NanotecWindow
from sputtering_app.runtime_settings import RuntimeSettings, default_runtime_settings, find_default_settings_file, load_runtime_settings


class NanotecStandaloneRuntime:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Nanotec Standalone Host")
        self.window: NanotecWindow | None = None

        self.runtime_settings = self._load_initial_runtime()
        self.controller = Controller(on_message=self._on_message, runtime=self.runtime_settings)

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
        self.window.set_controller(self.controller)
        try:
            old_controller.shutdown()
        except Exception:
            pass

    def _schedule_tick(self) -> None:
        if not self._alive:
            return
        self._tick_once()
        self.root.after(self._tick_ms, self._schedule_tick)

    def _tick_once(self) -> None:
        try:
            self.controller.tick()
            self.window.on_state_tick(self.controller.state)
        except Exception as exc:
            self._on_message(f"[ERR] standalone tick: {exc}")

    def _shutdown(self) -> None:
        self._alive = False
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
