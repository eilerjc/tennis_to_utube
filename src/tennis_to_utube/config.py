"""App configuration: shipped defaults merged with an optional user override file.

The override file is TOML at ``<user config dir>/config.toml``
(``%APPDATA%\\tennis_to_utube\\config.toml`` on Windows). It only needs the keys the
user wants to change. Problems in it (bad TOML, wrong types, unknown keys) produce
warnings; the app always starts with usable settings.

The Trim tool has its own file, ``trim.toml`` (:func:`load_trim_config`, same rules).

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
TRIM_CONFIG_FILENAME = "trim.toml"
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
    "scoring": {
        # Format for new matches (scoring.PRESETS): best of 3, 10-point match tiebreak.
        "default_format": "standard_mtb",
        # A Point pressed this soon after an ace/double fault is flagged as a possible duplicate.
        "duplicate_point_window_ms": 5000,
        # A shot button (winner/error) pressed this soon after a Point describes that point
        # instead of ending a new one.
        "shot_modifier_window_ms": 3000,
        # Fault, Let or Ace pressed this soon after a Serve (and nothing marked in between)
        # turns that serve into a fault/let/ace: press Serve at contact, then the call.
        "serve_call_window_ms": 6000,
    },
    # Short player names on buttons: first N letters of the first name + last initial.
    "names": {"short_first_letters": 4},
    "playback": {
        "speeds": [0.25, 0.5, 1.0, 1.5, 2.0, 4.0, 8.0],
        "skip_short_ms": 1000,
        "skip_long_ms": 5000,
        # Marks are moved earlier by this much real time (scaled by playback speed) to make
        # up for reaction time.
        "reaction_offset_ms": 200,
    },
}

# trim.toml — the Trim tool (DESIGN.md §9).
TRIM_DEFAULTS: dict[str, Any] = {
    # Removal rules proposed by default (see trim.RULES); a match can choose its own.
    "rules": ["warmup", "changeovers", "set_breaks", "after_match"],
    # Tiebreak changeover cuts end this long before the serve mark that resumes play.
    "serve_lead_in_ms": 3000,
    # The trimmed video's links/chapters: time = event time - lead-in (per event type).
    "lead_in_ms": {"default": 5000},
    "chapter_gap_ms": 600_000,
    # Made video's file name, next to the match file ({stem} = the match name).
    "output": {"name": "{stem} trimmed.mp4"},
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


def load_config(path: Path | None = None, *, defaults: dict[str, Any] = DEFAULTS,
                filename: str = CONFIG_FILENAME) -> Config:
    """Load defaults plus the user override file (default location if ``path`` is None)."""
    path = path if path is not None else user_config_dir() / filename
    data = copy.deepcopy(defaults)
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


def load_trim_config(path: Path | None = None) -> Config:
    """The Trim tool's settings: TRIM_DEFAULTS plus ``<user config dir>/trim.toml``."""
    return load_config(path, defaults=TRIM_DEFAULTS, filename=TRIM_CONFIG_FILENAME)


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
