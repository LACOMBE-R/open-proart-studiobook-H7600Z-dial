#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

try:
    from evdev import UInput, ecodes as e
    _EVDEV = True
except ImportError:
    _EVDEV = False

# Lazy-initialised uinput virtual keyboard (kernel-level, works on X11 + Wayland)
_uinput: "UInput | None" = None

def _get_uinput() -> "UInput | None":
    global _uinput
    if not _EVDEV:
        return None
    if _uinput is None:
        try:
            _uinput = UInput(
                {
                    e.EV_KEY: [
                        e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN, e.KEY_MUTE,
                        e.KEY_BRIGHTNESSUP, e.KEY_BRIGHTNESSDOWN,
                    ],
                    e.EV_REL: [e.REL_WHEEL, e.REL_WHEEL_HI_RES],
                },
                name="openknob-virtual-input",
            )
        except Exception:
            pass
    return _uinput


def _key(keycode: int) -> None:
    ui = _get_uinput()
    if ui is None:
        return
    ui.write(e.EV_KEY, keycode, 1)
    ui.write(e.EV_KEY, keycode, 0)
    ui.syn()


def _scroll(direction: int) -> None:
    ui = _get_uinput()
    if ui is None:
        return
    ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, direction * 120)
    ui.write(e.EV_REL, e.REL_WHEEL, direction)
    ui.syn()


_KEY_ACTIONS: dict[str, int] = {}
if _EVDEV:
    _KEY_ACTIONS = {
        "volume:up":       e.KEY_VOLUMEUP,
        "volume:down":     e.KEY_VOLUMEDOWN,
        "brightness:up":   e.KEY_BRIGHTNESSUP,
        "brightness:down": e.KEY_BRIGHTNESSDOWN,
    }


def _session_env() -> dict[str, str]:
    """Return env dict with user session vars, auto-detected when running as root."""
    env = dict(os.environ)
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        try:
            for uid_dir in sorted(Path("/run/user").iterdir()):
                bus = uid_dir / "bus"
                if bus.exists():
                    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
                    env.setdefault("XDG_RUNTIME_DIR", str(uid_dir))
                    env.setdefault("DISPLAY", ":1")
                    break
        except Exception:
            pass
    return env


def _run(cmd: list[str], user_session: bool = False) -> None:
    env = _session_env() if user_session else None
    try:
        subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def execute_action(action: str) -> bool:
    """Run an action. Returns True when action is 'next_function'."""
    if not action:
        return False
    if action == "next_function":
        return True
    if action in _KEY_ACTIONS:
        _key(_KEY_ACTIONS[action])
        return False
    if action == "scroll:up":
        _scroll(1)
        return False
    if action == "scroll:down":
        _scroll(-1)
        return False
    # Custom shell command
    try:
        _run(shlex.split(action), user_session=True)
    except ValueError:
        pass
    return False
