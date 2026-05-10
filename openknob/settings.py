#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import pwd
from pathlib import Path


def _real_home() -> Path:
    """Return the actual user's home even when running under sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except Exception:
            pass
    return Path.home()


CONFIG_DIR    = _real_home() / ".config" / "openknob"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PROFILES_DIR  = CONFIG_DIR / "profiles"

_DEFAULT: dict = {
    "overlay": {
        "position": "bottom-right",
        "margin": 20,
        "size": 220,
        "colors": {
            "ring_track": [80, 80, 100, 180],
            "ring_fill":  [40, 190, 255, 220],
            "background": [16, 16, 16, 200],
            "text":       [255, 255, 255, 255],
        },
    }
}


def _merge(base: dict, override: dict) -> dict:
    result = {**base}
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return _merge(_DEFAULT, json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return _merge({}, _DEFAULT)


def save_settings(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
