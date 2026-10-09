"""Flow state at the playhead (DESIGN.md §6): what the buttons should offer.

Everything is computed from the event log (via :mod:`scoring`) up to the playhead, so
seeking back and inserting a missed event just works.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import catalog, names
from .config import Config
from .matchfile import MatchFile
from .scoring import SHOT_TYPES, Analysis, ScoreView, analyze, resolve_format


def analyze_match(mf: MatchFile, config: Config | None = None) -> Analysis:
    """Score analysis of a match file, with its format (or the app default)."""
    default = config.get("scoring.default_format") if config else "standard_mtb"
    window = config.get("scoring.duplicate_point_window_ms") if config else 5000
    fmt, _ = resolve_format(mf.match.get("format"), default)
    return analyze(mf.events, fmt, window, mf.match)


@dataclass(frozen=True)
class Flow:
    score: ScoreView
    in_game: bool  # a Game start without its Game end yet
    set_can_end: bool  # the score says the set just ended (Set end not marked yet)
    match_over: bool
    next_server: str | None  # side expected to serve the next game
    next_server_player: str | None  # name of the expected server (None if not known)

    serve_pending: bool = False  # a Serve was just marked: its call (fault/let/ace) may follow

    def suggested(self) -> list[str]:
        """Action ids to put forward (others stay available, e.g. via the menu)."""
        if self.match_over:
            return ["match_end"]
        if self.set_can_end:
            return ["set_end_a", "set_end_b", "set_end_unknown"]
        if self.in_game and self.serve_pending:
            return ["fault", "let", "ace", "point_a", "point_b", "point_unknown"]
        if self.in_game:  # serves stay available but are not pushed (optional finer level)
            return ["point_a", "point_b", "point_unknown",
                    "game_end_a", "game_end_b", "game_end_unknown"]
        return ["game_start", "game_start_other_server", "set_start"]


# Marks that belong to a point (the latest one tells whether a serve is waiting for its call).
_POINT_MARKS = frozenset({catalog.POINT, catalog.ACE, catalog.FAULT, catalog.SERVE_IN,
                          catalog.LET, catalog.LET_POINT, *SHOT_TYPES})


def flow_at(analysis: Analysis, match: dict[str, Any], t_ms: int,
            serve_call_window_ms: int = 6000) -> Flow:
    steps = [st for st in analysis.steps if st.event.t_ms <= t_ms]
    view = steps[-1].view if steps else analysis.initial
    in_game = False
    for st in steps:
        if st.event.type == catalog.GAME_START:
            in_game = True
        elif st.event.type in (catalog.GAME_END, catalog.SET_END):
            in_game = False
    side = view.server
    point_marks = [st.event for st in steps if st.event.type in _POINT_MARKS]
    serve_pending = (bool(point_marks) and point_marks[-1].type == catalog.SERVE_IN
                     and t_ms - point_marks[-1].t_ms <= serve_call_window_ms)
    return Flow(
        score=view,
        in_game=in_game,
        set_can_end=view.pending_set is not None,
        match_over=view.winner is not None,
        next_server=side,
        next_server_player=_predict_player(steps, match, side),
        serve_pending=serve_pending,
    )


def _predict_player(steps, match: dict[str, Any], side: str | None) -> str | None:
    if side is None:
        return None
    if match.get("kind") != "doubles":
        return names.players(match, side)[0]
    # Doubles: partners alternate serving for their side within a set; at the start of a
    # set the team chooses, so there is no prediction until that side has served once.
    last: dict[str, str] = {}
    set_key: Any = object()
    for st in steps:
        key = len(st.view.sets) if st.view.sets is not None else None
        if key != set_key or st.event.type == catalog.SET_START:
            last = {}
            set_key = key
        if st.event.type == catalog.GAME_START:
            player_side = names.side_of(match, st.event.player)
            if player_side:
                last[player_side] = st.event.player
    return names.partner_of(match, last[side]) if side in last else None
