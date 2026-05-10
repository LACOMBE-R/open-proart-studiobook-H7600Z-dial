#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


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


def _brightness(direction: str) -> None:
    step_pct = 0.05
    backlight = Path("/sys/class/backlight")
    if not backlight.exists():
        return
    for dev in sorted(backlight.iterdir()):
        try:
            max_b = int((dev / "max_brightness").read_text().strip())
            cur_b = int((dev / "brightness").read_text().strip())
            step  = max(1, int(max_b * step_pct))
            new_b = cur_b + step if direction == "up" else cur_b - step
            new_b = max(max(1, max_b // 100), min(max_b, new_b))
            (dev / "brightness").write_text(str(new_b))
            return
        except PermissionError:
            # Not root — fall through to brightnessctl
            break
        except Exception:
            continue
    # Fallback: brightnessctl (user-mode)
    suffix = "5%+" if direction == "up" else "5%-"
    _run(["brightnessctl", "set", suffix])


_BUILTINS: dict[str, tuple[list[str], bool]] = {
    # (cmd, needs_user_session)
    "volume:up":       (["xdotool", "key", "--clearmodifiers", "XF86AudioRaiseVolume"], True),
    "volume:down":     (["xdotool", "key", "--clearmodifiers", "XF86AudioLowerVolume"], True),
    "brightness:up":   (["xdotool", "key", "--clearmodifiers", "XF86MonBrightnessUp"],  True),
    "brightness:down": (["xdotool", "key", "--clearmodifiers", "XF86MonBrightnessDown"],True),
    "scroll:up":       (["xdotool", "click", "--clearmodifiers", "4"],                  True),
    "scroll:down":     (["xdotool", "click", "--clearmodifiers", "5"],                  True),
}


def execute_action(action: str) -> bool:
    """Run an action. Returns True when action is 'next_function'."""
    if not action:
        return False
    if action == "next_function":
        return True
    entry = _BUILTINS.get(action)
    if entry is not None:
        cmd, session = entry
        _run(cmd, user_session=session)
        return False
    # Custom shell command
    try:
        _run(shlex.split(action), user_session=True)
    except ValueError:
        pass
    return False
