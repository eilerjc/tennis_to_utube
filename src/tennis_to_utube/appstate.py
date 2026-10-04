"""Remembered UI state: last folder, recent folders, favourite folders.

Kept in ``<user config dir>/state.json`` (separate from the hand-edited config.toml).
A missing or damaged file just means starting fresh; unknown fields are kept.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import user_config_dir

STATE_FILENAME = "state.json"
MAX_RECENT = 10


@dataclass
class AppState:
    last_folder: str | None = None
    recent: list[str] = field(default_factory=list)  # most recent first
    favorites: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def visit(self, folder: str | os.PathLike[str]) -> None:
        folder = str(folder)
        self.last_folder = folder
        self.recent = [folder] + [f for f in self.recent if f != folder][:MAX_RECENT - 1]

    def toggle_favorite(self, folder: str | os.PathLike[str]) -> bool:
        """Pin or unpin; returns True if the folder is now a favourite."""
        folder = str(folder)
        if folder in self.favorites:
            self.favorites.remove(folder)
            return False
        self.favorites.append(folder)
        return True


def state_path() -> Path:
    return user_config_dir() / STATE_FILENAME


def load_state(path: Path | None = None) -> AppState:
    path = path or state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppState()
    if not isinstance(data, dict):
        return AppState()

    def strings(value: Any) -> list[str]:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    last = data.get("last_folder")
    known = ("last_folder", "recent", "favorites")
    return AppState(last if isinstance(last, str) else None, strings(data.get("recent")),
                    strings(data.get("favorites")), {k: v for k, v in data.items() if k not in known})


def save_state(state: AppState, path: Path | None = None) -> None:
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_folder": state.last_folder, "recent": state.recent,
            "favorites": state.favorites, **state.extra}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
