#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".config" / "openknob" / "profiles"


@dataclass
class DialFunction:
    label: str
    icon: str = ""
    rotate_cw: str = ""
    rotate_ccw: str = ""
    press: str = "next_function"
    show_percentage: bool = True
    show_ring: bool = True


@dataclass
class Profile:
    name: str
    match: str = ".*"
    functions: list[DialFunction] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._pattern: re.Pattern = re.compile(self.match, re.IGNORECASE)

    def matches(self, app_name: str) -> bool:
        return bool(self._pattern.search(app_name))


def _parse_profile(data: dict, stem: str = "profile") -> Profile:
    functions = [
        DialFunction(
            label=f.get("label", ""),
            icon=f.get("icon", ""),
            rotate_cw=f.get("rotate_cw", ""),
            rotate_ccw=f.get("rotate_ccw", ""),
            press=f.get("press", "next_function"),
            show_percentage=f.get("show_percentage", True),
            show_ring=f.get("show_ring", True),
        )
        for f in data.get("functions", [])
    ]
    return Profile(
        name=data.get("name", stem),
        match=data.get("match", ".*"),
        functions=functions,
    )


def _default_profile() -> Profile:
    return Profile(
        name="default",
        match=".*",
        functions=[
            DialFunction(
                label="Volume",
                icon="audio-volume-high-symbolic",
                rotate_cw="volume:up",
                rotate_ccw="volume:down",
                press="next_function",
            ),
            DialFunction(
                label="Brightness",
                icon="display-brightness-symbolic",
                rotate_cw="brightness:up",
                rotate_ccw="brightness:down",
                press="next_function",
            ),
        ],
    )


def load_profiles(config_dir: Path = CONFIG_DIR) -> list[Profile]:
    config_dir.mkdir(parents=True, exist_ok=True)
    profiles: list[Profile] = []
    for path in sorted(config_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            profiles.append(_parse_profile(data, path.stem))
        except Exception:
            pass
    # Ensure a catch-all default at the end if no profiles loaded
    if not profiles:
        profiles.append(_default_profile())
    elif profiles[-1].match not in (".*", ""):
        profiles.append(_default_profile())
    return profiles


class ProfileManager:
    def __init__(self, config_dir: Path = CONFIG_DIR) -> None:
        self._lock = threading.Lock()
        self._config_dir = config_dir
        self._profiles = load_profiles(config_dir)
        self._active = self._profiles[0]
        self._func_index = 0
        self._dir_mtime: float = self._get_dir_mtime()

    def _get_dir_mtime(self) -> float:
        try:
            mtimes = [self._config_dir.stat().st_mtime] if self._config_dir.exists() else [0.0]
            for f in self._config_dir.glob("*.json"):
                mtimes.append(f.stat().st_mtime)
            return max(mtimes)
        except Exception:
            return 0.0

    def reload_if_changed(self) -> bool:
        """Reload profiles from disk if the directory has changed. Returns True on reload."""
        mtime = self._get_dir_mtime()
        if mtime == self._dir_mtime:
            return False
        self._dir_mtime = mtime
        new_profiles = load_profiles(self._config_dir)
        with self._lock:
            active_name = self._active.name
            self._profiles = new_profiles
            for p in new_profiles:
                if p.name == active_name:
                    self._active = p
                    if self._func_index >= len(p.functions):
                        self._func_index = 0
                    return True
            self._active = new_profiles[0]
            self._func_index = 0
        return True

    def match_app(self, app_name: str) -> bool:
        """Switch to the first matching profile. Returns True if profile changed."""
        with self._lock:
            for profile in self._profiles:
                if profile.matches(app_name):
                    if profile is self._active:
                        return False
                    self._active = profile
                    self._func_index = 0
                    return True
        return False

    @property
    def current_function(self) -> Optional[DialFunction]:
        with self._lock:
            if not self._active.functions:
                return None
            return self._active.functions[self._func_index % len(self._active.functions)]

    def next_function(self) -> Optional[DialFunction]:
        with self._lock:
            if not self._active.functions:
                return None
            self._func_index = (self._func_index + 1) % len(self._active.functions)
            return self._active.functions[self._func_index]

    @property
    def active_profile_name(self) -> str:
        with self._lock:
            return self._active.name

    @property
    def func_index(self) -> int:
        with self._lock:
            return self._func_index

    @property
    def func_count(self) -> int:
        with self._lock:
            return len(self._active.functions)
