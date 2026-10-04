"""App configuration: shipped defaults merged with an optional user override file.

The override file is TOML at ``<user config dir>/config.toml``
(``%APPDATA%\\tennis_to_utube\\config.toml`` on Windows). It only needs the keys the
user wants to change. Problems in it (bad TOML, wrong types, unknown keys) produce
warnings; the app always starts with usable settings.

Per-match overrides live in the match file's ``settings`` object and are layered on
top with :func:`effective_settings`.
"""

from __future__ import annotations

import copy
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "tennis_to_utube"
CONFIG_FILENAME = "config.toml"
# Overrides the user config directory (used by tests; handy for portable installs).
ENV_CONFIG_DIR = "TENNIS_TO_UTUBE_CONFIG_DIR"

DEFAULTS: dict[str, Any] = {
    "tools": {
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
    },
    # Link/chapter time = event time - lead-in. "default" applies to event types
    # without their own entry, e.g. ace = 3500.
    "lead_in_ms": {"default": 5000},
    # Add a chapter after this long without a Game/Set start (see DESIGN.md §8).
    "chapter_gap_ms": 600_000,
    "trim": {
        # Removal rules proposed by default in the trim pass (see trim.RULES).
        "rules": ["warmup", "changeovers", "set_breaks", "after_match"],
    },
    # Short player names on buttons: first N letters of the first name + last initial.
    "names": {"short_first_letters": 4},
    "playback": {
        "speeds": [0.25, 0.5, 1.0, 1.5, 2.0],
        "skip_short_ms": 1000,
        "skip_long_ms": 5000,
    },
}

# Tables whose keys are open-ended (one entry per event type). New keys are accepted
# if their value has the same type as the table's "default" entry.
_OPEN_TABLES = {("lead_in_ms",)}

# Match-file ``settings`` keys that override app config.
MATCH_SETTING_KEYS = ("lead_in_ms", "chapter_gap_ms")


def user_config_dir() -> Path:
    env = os.environ.get(ENV_CONFIG_DIR)
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


@dataclass
class Config:
    data: dict[str, Any]
    path: Path | None = None  # override file that was read, if any
    warnings: list[str] = field(default_factory=list)

    def get(self, dotted: str) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            node = node[part]
        return node


def load_config(path: Path | None = None) -> Config:
    """Load defaults plus the user override file (default location if ``path`` is None)."""
    path = path if path is not None else user_config_dir() / CONFIG_FILENAME
    data = copy.deepcopy(DEFAULTS)
    warnings: list[str] = []
    if not path.exists():
        return Config(data, None, warnings)
    try:
        user = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        warnings.append(f"{path}: could not read ({exc}); using defaults")
        return Config(data, None, warnings)
    _merge(data, user, (), warnings, str(path))
    return Config(data, path, warnings)


def _type_ok(default: Any, value: Any) -> bool:
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float))
    return isinstance(value, type(default))


def _merge(base: dict, user: dict, prefix: tuple[str, ...], warnings: list[str], origin: str) -> None:
    for key, value in user.items():
        where = ".".join(prefix + (key,))
        if key not in base:
            if prefix in _OPEN_TABLES and "default" in base:
                if _type_ok(base["default"], value):
                    base[key] = value
                else:
                    warnings.append(f"{origin}: {where} has the wrong type; ignored")
                continue
            # Unknown keys are kept (could be from a newer version) but flagged as a
            # likely typo.
            warnings.append(f"{origin}: unknown setting {where}")
            base[key] = value
            continue
        default = base[key]
        if isinstance(default, dict):
            if isinstance(value, dict):
                _merge(default, value, prefix + (key,), warnings, origin)
            else:
                warnings.append(f"{origin}: {where} should be a table; ignored")
        elif _type_ok(default, value):
            base[key] = value
        else:
            warnings.append(
                f"{origin}: {where} should be {type(default).__name__}, got "
                f"{type(value).__name__}; using default"
            )


@dataclass(frozen=True)
class Settings:
    """Effective per-match settings (app config + match-file overrides)."""

    lead_in: dict[str, int]
    chapter_gap_ms: int

    def lead_in_for(self, event_type: str) -> int:
        return int(self.lead_in.get(event_type, self.lead_in.get("default", 0)))


def effective_settings(config: Config, match_settings: dict[str, Any] | None = None) -> Settings:
    lead_in = dict(config.data["lead_in_ms"])
    gap = config.data["chapter_gap_ms"]
    match_settings = match_settings or {}
    override = match_settings.get("lead_in_ms")
    if isinstance(override, dict):
        lead_in.update({k: v for k, v in override.items() if isinstance(v, int) and not isinstance(v, bool)})
    override_gap = match_settings.get("chapter_gap_ms")
    if isinstance(override_gap, int) and not isinstance(override_gap, bool):
        gap = override_gap
    return Settings(lead_in=lead_in, chapter_gap_ms=gap)
