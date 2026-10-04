"""Actions and their keyboard shortcuts (DESIGN.md §11).

Every action is defined once here; the GUI builds both its button and its shortcut from
the definition, so they cannot drift. Defaults are the layout agreed with the owner
(US QWERTY, left hand on events, mouse in the right hand). A user override file,
``<user config dir>/shortcuts.toml``, can rebind or unbind any action::

    [keys]
    point_a = "F"            # one key
    fault = ["W", "Shift+F"] # several keys
    note = ""                # no key (button only)

Problems in the file (bad TOML, unknown actions, invalid keys, two actions on one key)
produce warnings; the app always starts with a usable, conflict-free set.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import catalog
from .config import user_config_dir

SHORTCUTS_FILENAME = "shortcuts.toml"

MODIFIERS = ("Ctrl", "Alt", "Shift", "Meta")
_MODIFIER_ALIASES = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
                     "meta": "Meta", "win": "Meta", "cmd": "Meta"}
NAMED_KEYS = (
    "Space", "Tab", "Enter", "Escape", "Backspace", "Delete", "Insert", "Home", "End",
    "PageUp", "PageDown", "Left", "Right", "Up", "Down",
    *(f"F{i}" for i in range(1, 13)),
)
_KEY_ALIASES = {
    **{k.lower(): k for k in NAMED_KEYS},
    "del": "Delete", "esc": "Escape", "return": "Enter", "pgup": "PageUp", "pgdown": "PageDown",
    "pgdn": "PageDown", "ins": "Insert", "backtick": "`", "comma": ",", "period": ".",
    "plus": "+", "minus": "-",
}
_PUNCTUATION = set("`-=[]\\;',./")


def normalize_key(text: str) -> str:
    """Canonical form, e.g. ``"shift+z"`` → ``"Shift+Z"``, ``"ctrl + left"`` → ``"Ctrl+Left"``.

    Raises ValueError for anything that is not one key with optional modifiers.
    """
    parts = [p.strip() for p in text.strip().split("+")]
    if len(parts) >= 2 and parts[-1] == parts[-2] == "":  # the "+" key itself: "+", "Ctrl++"
        parts = parts[:-2] + ["+"]
    if not parts or any(p == "" for p in parts):
        raise ValueError(f"not a key: {text!r}")
    *mods, key = parts
    canon_mods = set()
    for m in mods:
        if m.lower() not in _MODIFIER_ALIASES:
            raise ValueError(f"unknown modifier {m!r} in {text!r}")
        canon_mods.add(_MODIFIER_ALIASES[m.lower()])
    if len(key) == 1 and (key.isalnum() or key in _PUNCTUATION or key == "+"):
        canon_key = key.upper()
    elif key.lower() in _KEY_ALIASES:
        canon_key = _KEY_ALIASES[key.lower()]
    else:
        raise ValueError(f"unknown key {key!r} in {text!r}")
    return "+".join([m for m in MODIFIERS if m in canon_mods] + [canon_key])


@dataclass(frozen=True)
class Action:
    id: str
    label: str  # "{A}" / "{B}": the side's short names (names.short_side_names)
    group: str  # "point" | "serve" | "game" | "set" | "coaching" | "match" | "player" | "edit"
    keys: tuple[str, ...] = ()  # default keys
    event_type: str | None = None  # event actions: the type they log ...
    result: str | None = None  # ... and its result
    variant: str | None = None  # e.g. "other_server" for Game start with the other server
    side: str | None = None  # the side the event is about (shots: the hitter)

    def button_text(self, names: Mapping[str, str]) -> str:
        return self.label.replace("{A}", names.get("A", "A")).replace("{B}", names.get("B", "B"))


def _ev(id: str, label: str, group: str, keys: tuple[str, ...], event_type: str,
        result: str | None = None, variant: str | None = None, side: str | None = None) -> Action:
    return Action(id, label, group, keys, event_type, result, variant, side)


ACTIONS: tuple[Action, ...] = (
    # Points: home row
    _ev("point_a", "Point {A}", "point", ("A",), catalog.POINT, "A"),
    _ev("point_b", "Point {B}", "point", ("S",), catalog.POINT, "B"),
    _ev("point_unknown", "Point ?", "point", ("D",), catalog.POINT, "unknown"),
    # Serve: top row
    _ev("serve_in", "Serve in", "serve", ("Q",), catalog.SERVE_IN),
    _ev("fault", "Fault", "serve", ("W",), catalog.FAULT),
    _ev("let", "Let", "serve", ("E",), catalog.LET),
    _ev("ace", "Ace", "serve", ("R",), catalog.ACE),
    # Games: G and bottom row
    _ev("game_start", "Game start", "game", ("G",), catalog.GAME_START),
    _ev("game_start_other_server", "Game start (other server)", "game", ("Shift+G",),
        catalog.GAME_START, variant="other_server"),
    _ev("game_end_a", "Game {A}", "game", ("Z",), catalog.GAME_END, "A"),
    _ev("game_end_b", "Game {B}", "game", ("X",), catalog.GAME_END, "B"),
    _ev("game_end_unknown", "Game ?", "game", ("C",), catalog.GAME_END, "unknown"),
    # Sets
    _ev("set_start", "Set start", "set", ("T",), catalog.SET_START),
    _ev("score_state", "Set score…", "set", ("Shift+T",), catalog.SCORE_STATE),
    _ev("set_end_a", "Set {A}", "set", ("Shift+Z",), catalog.SET_END, "A"),
    _ev("set_end_b", "Set {B}", "set", ("Shift+X",), catalog.SET_END, "B"),
    _ev("set_end_unknown", "Set ?", "set", ("Shift+C",), catalog.SET_END, "unknown"),
    # Shots: buttons only (no keys, agreed); end the point like an ace
    _ev("winner_a", "Winner {A}", "shot", (), catalog.WINNER, side="A"),
    _ev("forced_error_a", "Forced err. {A}", "shot", (), catalog.FORCED_ERROR, side="A"),
    _ev("unforced_error_a", "Unforced err. {A}", "shot", (), catalog.UNFORCED_ERROR, side="A"),
    _ev("winner_b", "Winner {B}", "shot", (), catalog.WINNER, side="B"),
    _ev("forced_error_b", "Forced err. {B}", "shot", (), catalog.FORCED_ERROR, side="B"),
    _ev("unforced_error_b", "Unforced err. {B}", "shot", (), catalog.UNFORCED_ERROR, side="B"),
    # Coaching marks: number row
    _ev("good_recovery", "Good recovery", "coaching", ("1",), catalog.GOOD_RECOVERY),
    _ev("footwork", "Footwork", "coaching", ("2",), catalog.FOOTWORK),
    _ev("body_language", "Body language", "coaching", ("3",), catalog.BODY_LANGUAGE),
    _ev("late_contact", "Late contact", "coaching", ("4",), catalog.LATE_CONTACT),
    _ev("strategy", "Strategy", "coaching", ("5",), catalog.STRATEGY),
    _ev("note", "Note…", "coaching", ("6",), catalog.NOTE),
    # Rare: buttons/menu only
    _ev("match_start", "Match start", "match", (), catalog.MATCH_START),
    _ev("match_end", "Match end", "match", (), catalog.MATCH_END),
    _ev("tiebreak_start", "Tiebreak start", "match", (), catalog.TIEBREAK_START),
    _ev("rules_change", "Rules change…", "match", (), catalog.RULES_CHANGE),
    _ev("ending_state", "Ending state…", "match", (), catalog.ENDING_STATE),
    # Player: Space and arrows (speed is set with buttons only)
    Action("play_pause", "Play/pause", "player", ("Space",)),
    Action("skip_back", "Back (long)", "player", ("Left",)),
    Action("skip_forward", "Forward (long)", "player", ("Right",)),
    Action("skip_back_short", "Back (short)", "player", ("Shift+Left",)),
    Action("skip_forward_short", "Forward (short)", "player", ("Shift+Right",)),
    Action("frame_back", "Frame back", "player", ("Ctrl+Left",)),
    Action("frame_forward", "Frame forward", "player", ("Ctrl+Right",)),
    Action("prev_event", "Previous event", "player", ("Up",)),
    Action("next_event", "Next event", "player", ("Down",)),
    # Editing
    Action("undo", "Undo", "edit", ("Ctrl+Z",)),
    Action("redo", "Redo", "edit", ("Ctrl+Y",)),
    Action("nudge_back", "Move event 1 frame back", "edit", ("Alt+Left",)),
    Action("nudge_forward", "Move event 1 frame forward", "edit", ("Alt+Right",)),
    Action("delete_event", "Delete event", "edit", ("Delete",)),
    Action("lock_events", "Lock events", "edit", ("Ctrl+L",)),
)
ACTIONS_BY_ID = {a.id: a for a in ACTIONS}


@dataclass
class Shortcuts:
    keys: dict[str, tuple[str, ...]]  # action id -> keys (each key used once overall)
    path: Path | None = None  # override file that was read, if any
    warnings: list[str] = field(default_factory=list)

    def action_for(self, key: str) -> Action | None:
        key = normalize_key(key)
        for action_id, keys in self.keys.items():
            if key in keys:
                return ACTIONS_BY_ID[action_id]
        return None

    def keys_for(self, action_id: str) -> tuple[str, ...]:
        return self.keys.get(action_id, ())


def default_shortcuts() -> Shortcuts:
    return Shortcuts({a.id: a.keys for a in ACTIONS})


def load_shortcuts(path: Path | None = None) -> Shortcuts:
    path = path if path is not None else user_config_dir() / SHORTCUTS_FILENAME
    if not path.exists():
        return default_shortcuts()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        sc = default_shortcuts()
        sc.warnings.append(f"{path}: could not read ({exc}); using default keys")
        return sc
    sc = apply_overrides(data.get("keys", {}), origin=str(path))
    unknown_tables = sorted(set(data) - {"keys"})
    if unknown_tables:
        sc.warnings.append(f"{path}: unknown sections {unknown_tables}; only [keys] is used")
    sc.path = path
    return sc


def apply_overrides(overrides: Mapping[str, Any], origin: str = "shortcuts") -> Shortcuts:
    """Defaults with ``overrides`` applied; user bindings win over default ones."""
    warnings: list[str] = []
    user: dict[str, tuple[str, ...]] = {}
    if not isinstance(overrides, Mapping):
        warnings.append(f"{origin}: [keys] should be a table; using default keys")
        overrides = {}
    for action_id, value in overrides.items():
        if action_id not in ACTIONS_BY_ID:
            warnings.append(f"{origin}: unknown action {action_id!r}; ignored")
            continue
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            warnings.append(f"{origin}: {action_id} should be a key or a list of keys; ignored")
            continue
        keys = []
        for v in values:
            if v.strip() == "":
                continue  # "" = no key
            try:
                keys.append(normalize_key(v))
            except ValueError as exc:
                warnings.append(f"{origin}: {action_id}: {exc}; ignored")
        user[action_id] = tuple(dict.fromkeys(keys))

    # Resolve each key to one action: user bindings first (in file order), then defaults.
    owner: dict[str, str] = {}
    result: dict[str, list[str]] = {a.id: [] for a in ACTIONS}
    for action_id, keys in user.items():
        for k in keys:
            if k in owner:
                warnings.append(f"{origin}: {k} is bound to both {owner[k]} and {action_id}; "
                                f"kept for {owner[k]}")
                continue
            owner[k] = action_id
            result[action_id].append(k)
    for a in ACTIONS:
        if a.id in user:
            continue
        for k in a.keys:
            if k in owner:
                warnings.append(f"{origin}: {k} now belongs to {owner[k]}, so {a.id} has no key "
                                f"(was {k})")
                continue
            owner[k] = a.id
            result[a.id].append(k)
    return Shortcuts({k: tuple(v) for k, v in result.items()}, warnings=warnings)
