#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

POLL_INTERVAL = 0.5

try:
    from Xlib import display as _xdisplay
    from Xlib.ext import ewmh as _xewmh
    _XLIB_AVAILABLE = True
except ImportError:
    _XLIB_AVAILABLE = False


def _gdbus_active_app() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell",
                "--object-path", "/org/gnome/Shell",
                "--method", "org.gnome.Shell.Eval",
                "global.display.focus_window?.get_wm_class() ?? ''",
            ],
            capture_output=True, text=True, timeout=1,
        )
        out = result.stdout.strip()
        if "'true'" not in out and '"true"' not in out:
            return None
        # Parse gdbus output: "('true', 'ClassName',)"
        parts = out.split(",")
        for part in parts[1:]:
            val = part.strip().strip("()\"', ")
            if val and val not in ("true", "false"):
                return val.lower()
    except Exception:
        pass
    return None


def _xlib_active_app() -> Optional[str]:
    if not _XLIB_AVAILABLE:
        return None
    try:
        d = _xdisplay.Display()
        e = _xewmh.EWMH(d)
        win = e.getActiveWindow()
        if win is None:
            return None
        pid = e.getWmPid(win)
        if pid:
            comm_path = Path(f"/proc/{pid}/comm")
            if comm_path.exists():
                return comm_path.read_text().strip().lower()
        wm_class = win.get_wm_class()
        if wm_class:
            return wm_class[-1].lower()
    except Exception:
        pass
    return None


def _xprop_active_app() -> Optional[str]:
    """Fallback X11 detection using xprop (no python-xlib required)."""
    try:
        r1 = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True, text=True, timeout=1,
        )
        line = r1.stdout.strip()
        if "0x" not in line:
            return None
        wid = "0x" + line.split("0x")[-1].split(",")[0].strip()
        r2 = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS"],
            capture_output=True, text=True, timeout=1,
        )
        out = r2.stdout.strip()
        if "=" in out:
            parts = out.split("=")[1].strip().split(",")
            if parts:
                return parts[-1].strip().strip('"').lower()
    except Exception:
        pass
    return None


def get_active_app() -> Optional[str]:
    if os.environ.get("WAYLAND_DISPLAY"):
        return _gdbus_active_app()
    if _XLIB_AVAILABLE:
        return _xlib_active_app()
    return _xprop_active_app()


class WindowWatcher:
    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._on_change = on_change
        self._last_app: Optional[str] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="window-watcher")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                app = get_active_app()
                if app and app != self._last_app:
                    self._last_app = app
                    self._on_change(app)
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
