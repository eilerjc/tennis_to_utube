"""What the timeline bars show, independent of Qt: bands, tick categories, view geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from . import catalog, structure
from .matchfile import Event

# Tick categories (colours are chosen by the GUI).
CATEGORIES = {
    catalog.POINT: "point",
    catalog.SERVE_IN: "serve", catalog.FAULT: "serve", catalog.LET: "serve", catalog.ACE: "serve",
    catalog.WINNER: "shot", catalog.FORCED_ERROR: "shot", catalog.UNFORCED_ERROR: "shot",
    catalog.GOOD_RECOVERY: "coaching", catalog.FOOTWORK: "coaching",
    catalog.BODY_LANGUAGE: "coaching", catalog.LATE_CONTACT: "coaching",
    catalog.STRATEGY: "coaching", catalog.NOTE: "note",
    catalog.GAME_START: "game", catalog.GAME_END: "game", catalog.TIEBREAK_START: "game",
    catalog.SET_START: "set", catalog.SET_END: "set", catalog.SCORE_STATE: "set",
    catalog.MATCH_START: "match", catalog.MATCH_END: "match", catalog.RULES_CHANGE: "match",
    catalog.ENDING_STATE: "match",
}


def category(e: Event) -> str:
    return CATEGORIES.get(e.type, "other")


@dataclass(frozen=True)
class Band:
    start_ms: int
    end_ms: int
    kind: str  # "set" | "game"
    label: str
    index: int  # alternating shade


def bands(events: Iterable[Event], total_ms: int) -> list[Band]:
    """Game bands (Game start → its end) and set bands (first game/Set start → Set end)."""
    out: list[Band] = []
    game: tuple[int, str] | None = None
    set_start: int | None = None
    set_label = ""
    n_game = n_set = 0
    for e, pos in structure.walk(events):
        t = e.t_ms
        if e.type == catalog.GAME_START:
            if game is not None:
                out.append(Band(game[0], t, "game", game[1], n_game))
                n_game += 1
            game = (t, f"G{pos.game_no}" if pos.game_no else "G")
            if set_start is None:
                set_start, set_label = t, f"Set {pos.set_no}" if pos.set_no else "Set"
        elif e.type in (catalog.GAME_END, catalog.SET_END) and game is not None:
            out.append(Band(game[0], t, "game", game[1], n_game))
            n_game += 1
            game = None
        if e.type == catalog.SET_START:
            if set_start is not None and set_start < t:
                out.append(Band(set_start, t, "set", set_label, n_set))
                n_set += 1
            set_start, set_label = t, f"Set {pos.set_no}" if pos.set_no else "Set"
        elif e.type == catalog.SET_END and set_start is not None:
            out.append(Band(set_start, t, "set", set_label, n_set))
            n_set += 1
            set_start = None
    if game is not None:
        out.append(Band(game[0], total_ms, "game", game[1], n_game))
    if set_start is not None:
        out.append(Band(set_start, total_ms, "set", set_label, n_set))
    return [b for b in out if b.end_ms > b.start_ms]


@dataclass
class View:
    """Visible window of the timeline: ``[start_ms, start_ms + span_ms)``."""

    start_ms: float
    span_ms: float
    total_ms: int
    min_span_ms: float = 2000.0

    def x_of(self, t_ms: float, width: int) -> float:
        return (t_ms - self.start_ms) / self.span_ms * width

    def t_of(self, x: float, width: int) -> int:
        t = self.start_ms + x / max(1, width) * self.span_ms
        return int(min(max(0, round(t)), self.total_ms))

    def clamp(self) -> None:
        self.span_ms = min(max(self.span_ms, self.min_span_ms), max(self.total_ms, self.min_span_ms))
        self.start_ms = min(max(0.0, self.start_ms), max(0.0, self.total_ms - self.span_ms))

    def zoom(self, factor: float, anchor_ms: float) -> None:
        """Zoom by ``factor`` (>1 = in) keeping ``anchor_ms`` at the same place on screen."""
        frac = (anchor_ms - self.start_ms) / self.span_ms
        self.span_ms /= factor
        self.clamp()
        self.start_ms = anchor_ms - frac * self.span_ms
        self.clamp()

    def follow(self, t_ms: float, margin: float = 0.1) -> bool:
        """Scroll so ``t_ms`` stays visible (page forward/back); True if the view moved."""
        lo = self.start_ms + self.span_ms * margin
        hi = self.start_ms + self.span_ms * (1 - margin)
        if lo <= t_ms <= hi or (t_ms < lo and self.start_ms == 0):
            return False
        old = self.start_ms
        self.start_ms = t_ms - self.span_ms * margin if t_ms > hi else t_ms - self.span_ms * (1 - margin)
        self.clamp()
        return self.start_ms != old


def nearest_event(events: Sequence[Event], t_ms: int, tolerance_ms: float) -> Event | None:
    best, best_d = None, tolerance_ms
    for e in events:
        d = abs(e.t_ms - t_ms)
        if d <= best_d:
            best, best_d = e, d
    return best
