from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _is_problematic_macos_python() -> bool:
    if sys.platform != "darwin":
        return False
    exe = str(Path(sys.executable).resolve())
    return (
        exe == "/usr/bin/python3"
        or "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework" in exe
    )


def _macos_fallback_pythons() -> list[str]:
    candidates = []
    preferred = os.getenv("SPUTTER_PREFERRED_PYTHON")
    if preferred:
        candidates.append(preferred)
    candidates.extend(
        [
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3.13",
            "/usr/local/bin/python3.12",
            "/usr/local/bin/python3",
        ]
    )
    return candidates


def _auto_switch_python_for_macos_gui(args: argparse.Namespace) -> None:
    if args.check or args.list_ports or args.show_runtime or args.save_settings_template:
        return
    if not _is_problematic_macos_python():
        return
    if os.getenv("SPUTTER_PYTHON_SWITCHED") == "1":
        return

    current = str(Path(sys.executable).resolve())
    for candidate in _macos_fallback_pythons():
        p = Path(candidate)
        if not p.exists():
            continue
        try:
            resolved = str(p.resolve())
        except Exception:
            resolved = str(p)
        if resolved == current:
            continue
        env = os.environ.copy()
        env["SPUTTER_PYTHON_SWITCHED"] = "1"
        os.execve(resolved, [resolved, *sys.argv], env)

    print(
        "This macOS system Python cannot start Tk on your OS version.\n"
        "Install Homebrew Python and start again:\n"
        "  brew install python@3.12 python-tk@3.12\n"
        "Then run:\n"
        "  /opt/homebrew/bin/python3.12 run.py",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sputtering Facility Python rewrite launcher")
    parser.add_argument("--check", action="store_true", help="import core modules and exit")
    parser.add_argument("--list-ports", action="store_true", help="show available serial ports and exit")
    parser.add_argument("--show-runtime", action="store_true", help="print runtime info and exit")
    parser.add_argument(
        "--settings",
        help="path to a JSON settings file (mode, ports, Pfeiffer backend mapping)",
    )
    parser.add_argument(
        "--save-settings-template",
        metavar="PATH",
        help="write effective runtime settings to JSON and exit",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--simulation",
        action="store_true",
        help="force simulation mode for this run (equivalent to SPUTTER_SIMULATION=true)",
    )
    mode_group.add_argument(
        "--real",
        action="store_true",
        help="force real hardware mode for this run (equivalent to SPUTTER_SIMULATION=false)",
    )
    args = parser.parse_args()

    from sputtering_app.runtime_settings import (
        default_runtime_settings,
        find_default_settings_file,
        load_runtime_settings,
        save_runtime_settings,
    )

    runtime_settings = default_runtime_settings()
    settings_path: Path | None = None

    if args.settings:
        settings_path = Path(args.settings).expanduser().resolve()
    else:
        settings_path = find_default_settings_file(Path.cwd())

    if settings_path is not None:
        try:
            runtime_settings = load_runtime_settings(settings_path, base=runtime_settings)
        except Exception as exc:
            print(f"Failed to load settings file '{settings_path}': {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    if args.simulation:
        runtime_settings = runtime_settings.with_simulation(True)
    elif args.real:
        runtime_settings = runtime_settings.with_simulation(False)

    _auto_switch_python_for_macos_gui(args)

    if args.check:
        from sputtering_app import config, controller, models, protocols  # noqa: F401
        return

    if args.show_runtime:
        print(f"python={sys.executable}")
        print(f"version={sys.version.split()[0]}")
        print(f"platform={sys.platform}")
        print(f"settings_file={settings_path if settings_path is not None else '(none)'}")
        print(f"simulation={runtime_settings.simulation}")
        print(f"pfeiffer_controller={runtime_settings.pfeiffer_controller}")
        print(f"pfeiffer_single_gauge={runtime_settings.pfeiffer_single_gauge}")
        print(f"pfeiffer_maxi_chamber_channel={runtime_settings.pfeiffer_maxi_chamber_channel}")
        print(f"pfeiffer_maxi_load_channel={runtime_settings.pfeiffer_maxi_load_channel}")
        for key in ("nanotec", "dualg", "fug", "pinnacle", "expert"):
            print(f"port_{key}={runtime_settings.ports.get(key, '')}")
        return

    if args.save_settings_template:
        out_path = save_runtime_settings(args.save_settings_template, runtime_settings)
        print(f"Settings template written: {out_path}")
        return

    if args.list_ports:
        from sputtering_app.io_backends import list_serial_ports

        ports = list_serial_ports()
        if ports:
            for name in ports:
                print(name)
        else:
            print("No serial ports found (or pyserial is not installed).")
        return

    try:
        from sputtering_app.gui import App
    except Exception as exc:
        print(f"Failed to start GUI: {exc}", file=sys.stderr)
        print("Install a Python build with Tk support and run again.", file=sys.stderr)
        raise SystemExit(1) from exc

    app = App(initial_runtime=runtime_settings, runtime_path=settings_path)
    app.mainloop()


if __name__ == "__main__":
    main()
