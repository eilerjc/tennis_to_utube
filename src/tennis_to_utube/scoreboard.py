"""What the burned-in scoreboard shows, and when (DESIGN.md §9a).

Pure Python (no Pillow, no ffmpeg), used by the Overlay tool. A :class:`Board` is built
from the score engine's view after each event; :func:`board_changes` lists them on the
joined timeline and :func:`boards_on` lays them on a made video's timeline through its
trim plan (the full recording uses a plan without cuts).

Only what the engine is certain of is shown; unknown parts are blank. Before any score is
known the board shows 0s.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from . import catalog, names
from .matchfile import Event
from .scoring import SHOT_TYPES, Analysis, ScoreView, SetScore, point_server_side
from .trim import TrimPlan

# Marks that show the match is scored point by point (the points column is shown).
POINT_MARKS = frozenset({catalog.POINT, catalog.ACE, catalog.FAULT, *SHOT_TYPES})
POINTS_SETTINGS = ("auto", "on", "off")
_POINT_NAMES = ("0", "15", "30", "40")


@dataclass(frozen=True)
class Cell:
    """One side's games in a completed set."""

    text: str
    sup: str = ""  # small raised number: the tiebreak points of the set's loser
    won: bool = False  # this side won the set


@dataclass(frozen=True)
class Board:
    names: tuple[str, str]  # side A, side B; doubles "Emma Jones / Sara Smith"
    sets: tuple[tuple[Cell, Cell], ...] = ()  # completed sets
    games: tuple[str, str] | None = ("0", "0")  # current set; None after the match
    points: tuple[str, str] | None = None  # current game; None when not shown
    server: str | None = None  # side serving the next point
    winner: str | None = None  # match winner


@dataclass(frozen=True)
class Shown:
    """A board on screen over ``[start_ms, end_ms)`` of the video."""

    start_ms: int
    end_ms: int
    board: Board


def side_names(match: dict[str, Any]) -> tuple[str, str]:
    """Full names as typed, doubles partners joined by " / "."""
    return tuple(" / ".join(names.players(match, side)) for side in names.SIDES)  # type: ignore[return-value]


def shows_points(events: Iterable[Event], setting: str = "auto") -> bool:
    """Points column: always ("on"), never ("off"), or when the match has point marks."""
    if setting in ("on", "off"):
        return setting == "on"
    return any(e.type in POINT_MARKS for e in events)


def _set_cells(s: SetScore) -> tuple[Cell, Cell]:
    won = (s.winner == "A", s.winner == "B")
    if s.games is None:  # recorded at set level, without the games
        mark = {"A": ("W", "L"), "B": ("L", "W")}.get(s.winner or "", ("?", "?"))
        return Cell(mark[0], won=won[0]), Cell(mark[1], won=won[1])
    a, b = s.games
    if s.tiebreak is not None and (a, b) in ((1, 0), (0, 1)):  # a match tiebreak: its points
        return Cell(str(s.tiebreak[0]), won=won[0]), Cell(str(s.tiebreak[1]), won=won[1])
    sup = ("", "")
    if s.tiebreak is not None:
        lost = str(min(s.tiebreak))
        sup = (lost, "") if b > a else ("", lost)
    return Cell(str(a), sup[0], won[0]), Cell(str(b), sup[1], won[1])


def points_cells(view: ScoreView) -> tuple[str, str]:
    """ "15"/"30"/"40"/"AD" per side (counts in a tiebreak); blank if unknown."""
    if view.points is None or view.in_tiebreak is None:
        return ("", "")
    a, b = view.points
    if view.in_tiebreak:
        return (str(a), str(b))
    if a >= 3 and b >= 3:
        if a == b or not view.fmt.ad:
            return ("40", "40")
        return ("AD", "40") if a > b else ("40", "AD")
    return (_POINT_NAMES[min(a, 3)], _POINT_NAMES[min(b, 3)])


def board_of(view: ScoreView, names_: tuple[str, str], show_points: bool) -> Board:
    sets = tuple(_set_cells(s) for s in view.sets) if view.sets is not None else ()
    if view.winner is not None:
        return Board(names_, sets, games=None, points=None, server=None, winner=view.winner)
    games = (str(view.games[0]), str(view.games[1])) if view.games is not None else ("", "")
    points = points_cells(view) if show_points else None
    return Board(names_, sets, games, points, point_server_side(view))


def board_changes(analysis: Analysis, match: dict[str, Any], show_points: bool,
                  delay_ms: int = 0) -> list[tuple[int, Board]]:
    """(time on the joined timeline, board shown from then on); the first is at 0.

    The board changes at the event that changed the score, plus ``delay_ms``.
    """
    names_ = side_names(match)
    out = [(0, board_of(analysis.initial, names_, show_points))]
    for st in analysis.steps:
        t = max(0, st.event.t_ms + delay_ms)
        board = board_of(st.view, names_, show_points)
        if t <= out[-1][0]:
            out[-1] = (out[-1][0], board)  # same moment: the later event's score wins
        else:
            out.append((t, board))
    collapsed = [out[0]]
    for t, board in out[1:]:
        if board != collapsed[-1][1]:
            collapsed.append((t, board))
    return collapsed


def boards_on(plan: TrimPlan, changes: Sequence[tuple[int, Board]], start_ms: int = 0,
              end_ms: int | None = None) -> list[Shown]:
    """Boards over the made video, from ``start_ms`` to ``end_ms`` (output ms; default: to
    its end), with times relative to ``start_ms``. The first starts at 0, the last runs to
    the end."""
    video_end = plan.output_start_ms + plan.total_out_ms
    end_ms = video_end if end_ms is None else min(end_ms, video_end)
    times = [t for t, _ in changes]
    shown: list[Shown] = []

    def add(a: int, b: int, board: Board) -> None:
        a, b = max(a, start_ms) - start_ms, min(b, end_ms) - start_ms
        if b <= a:
            return
        if shown and shown[-1].board == board and shown[-1].end_ms >= a:
            shown[-1] = Shown(shown[-1].start_ms, b, board)
        else:
            shown.append(Shown(a, b, board))

    for seg in plan.segments:
        out0 = plan.output_start_ms + seg.out_start_ms
        i = max(0, bisect_right(times, seg.start_ms) - 1)
        while i < len(changes) and times[i] < seg.end_ms:
            a = max(times[i], seg.start_ms)
            b = min(times[i + 1], seg.end_ms) if i + 1 < len(changes) else seg.end_ms
            add(out0 + a - seg.start_ms, out0 + b - seg.start_ms, changes[i][1])
            i += 1
    if shown:  # cover the very start (a muxer offset) and the very end
        shown[0] = Shown(0, shown[0].end_ms, shown[0].board)
        shown[-1] = Shown(shown[-1].start_ms, end_ms - start_ms, shown[-1].board)
    return shown


def board_at(shown: Sequence[Shown], t_ms: int) -> Board | None:
    for s in shown:
        if s.start_ms <= t_ms < s.end_ms:
            return s.board
    return shown[-1].board if shown and t_ms >= shown[-1].end_ms else None
