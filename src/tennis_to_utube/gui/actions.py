"""QActions built from the action definitions in :mod:`..shortcuts` (one source for keys,
buttons and menus, so they cannot drift)."""

from __future__ import annotations

from typing import Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

from ..shortcuts import ACTIONS, Shortcuts

# Keys that may repeat while held down.
REPEATING = {"frame_back", "frame_forward", "skip_back", "skip_forward", "skip_back_short",
             "skip_forward_short", "nudge_back", "nudge_forward"}

# Arrows etc. shown compactly on buttons.
_KEY_GLYPHS = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "Delete": "Del",
               "Space": "Space"}


def key_text(key: str) -> str:
    """Short label for a key, e.g. "Shift+Left" → "Shift+←"."""
    parts = key.split("+")
    if key.endswith("++"):
        parts = parts[:-2] + ["+"]
    return "+".join(_KEY_GLYPHS.get(p, p) for p in parts)


def build_actions(owner: QWidget, shortcuts: Shortcuts,
                  handlers: Mapping[str, Callable[[], None]]) -> dict[str, QAction]:
    """One QAction per action id with a handler; shortcuts work while ``owner`` (or a child)
    has focus."""
    actions = {}
    for a in ACTIONS:
        if a.id not in handlers:
            continue
        act = QAction(a.label, owner)
        act.setShortcuts([QKeySequence(k) for k in shortcuts.keys_for(a.id)])
        act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act.setAutoRepeat(a.id in REPEATING)
        act.triggered.connect(handlers[a.id])
        owner.addAction(act)
        actions[a.id] = act
    return actions
