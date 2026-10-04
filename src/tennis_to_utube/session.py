"""An open match being marked: edits, undo/redo, lock, analysis and saving (no Qt).

Every edit goes through :class:`Session` so it can be undone. Undo restores a snapshot of
the whole match file (small), but never moves the event-id counter back, so ids stay unique.
"""

from __future__ import annotations

import copy
import dataclasses
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import catalog, matchfile, names
from .config import Config
from .flow import Flow, analyze_match, flow_at
from .matchfile import Event, MatchFile
from .scoring import (SHOT_TYPES, Analysis, other, point_server_side,  # noqa: F401 (re-exported)
                      shot_point_winner)
from .shortcuts import ACTIONS_BY_ID
from .timeline import Timeline
from .trim import RULES, Cut, KeyframeLookup, TrimPlan, plan_from_dict, plan_trim, propose_cuts

MAX_UNDO = 500
SHOT_MODIFIER_WINDOW_MS = 3000
# Marks that belong to a point: the latest one before a shot press decides whether the shot
# describes the last Point (a serve mark means a new point has started).
_POINT_MARKS = (catalog.POINT, catalog.ACE, catalog.FAULT, catalog.SERVE_IN, catalog.LET, *SHOT_TYPES)


class LockedError(RuntimeError):
    pass


class Session:
    def __init__(self, mf: MatchFile, path: str | Path, config: Config | None = None):
        self.mf = mf
        self.path = Path(path)
        self.config = config
        self.locked = False
        self.dirty = False
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._analysis: Analysis | None = None
        self._batch = 0

    # -- derived state -------------------------------------------------------------

    @property
    def analysis(self) -> Analysis:
        if self._analysis is None:
            self._analysis = analyze_match(self.mf, self.config)
        return self._analysis

    def flow_at(self, t_ms: int) -> Flow:
        return flow_at(self.analysis, self.mf.match, t_ms)

    def event(self, event_id: str) -> Event:
        return self.mf.event(event_id)

    @property
    def total_ms(self) -> int:
        return Timeline.from_sources(self.mf.sources).total_ms if self.mf.sources else 0

    def timeline(self) -> Timeline:
        return Timeline.from_sources(self.mf.sources)

    def source_paths(self) -> list[Path]:
        return [matchfile.resolve_source_path(s.path, self.path) for s in self.mf.sources]

    # -- trimming and the produced video ---------------------------------------------------

    def plan(self, keyframes: KeyframeLookup | None) -> TrimPlan:
        """Exact plan for the enabled cuts (``keyframes`` may be None without cuts)."""
        cuts = self.cuts()
        return plan_trim(self.timeline(), cuts, keyframes if any(c.enabled for c in cuts) else None)

    def set_output(self, data: dict[str, Any] | None) -> None:
        self._before_edit()
        self.mf.output = data
        self._after_edit()

    def output_plan(self) -> TrimPlan | None:
        """Plan of the video last made (for export), or None."""
        if not self.mf.output or not self.mf.sources:
            return None
        try:
            return plan_from_dict(self.mf.output, self.timeline())
        except (KeyError, TypeError, ValueError):
            return None

    def export_plan(self) -> tuple[TrimPlan, str]:
        """The timeline links refer to, and a note for the user."""
        plan = self.output_plan()
        if plan is not None:
            name = Path((self.mf.output or {}).get("path") or "the video").name
            return plan, f"Times refer to {name} (made on the Trim step)."
        note = "No video made on the Trim step yet: times refer to the original recording"
        if len(self.mf.sources) > 1:
            note += " joined in order (make a video on the Trim step to upload one file)"
        return plan_trim(self.timeline(), [], None), note + "."

    def set_video_id(self, video_id: str | None) -> None:
        self._before_edit()
        self.mf.youtube["video_id"] = video_id
        self._after_edit()

    def output_path(self) -> Path | None:
        path = (self.mf.output or {}).get("path")
        return matchfile.resolve_source_path(path, self.path) if path else None

    # -- trim choices (stored in the match file's settings.trim) ----------------------

    def _trim_settings(self) -> dict[str, Any]:
        trim = self.mf.settings.get("trim")
        return trim if isinstance(trim, dict) else {}

    def trim_rules(self) -> list[str]:
        rules = self._trim_settings().get("rules")
        if not isinstance(rules, list):
            rules = list(self.config.get("trim.rules")) if self.config else list(RULES)
        return [r for r in rules if r in RULES]

    def cuts(self) -> list[Cut]:
        """Proposed cuts for the chosen rules; unticked ones have ``enabled=False``."""
        unticked = set(self._trim_settings().get("unticked", []))
        return [dataclasses.replace(c, enabled=c.key not in unticked)
                for c in propose_cuts(self.mf.events, self.total_ms, self.trim_rules(),
                                      self.analysis)]

    def set_trim_rules(self, rules: list[str]) -> None:
        self._before_edit()
        self.mf.settings.setdefault("trim", {})["rules"] = [r for r in RULES if r in rules]
        self._after_edit()

    def set_cut_enabled(self, key: str, enabled: bool) -> None:
        self._before_edit()
        trim = self.mf.settings.setdefault("trim", {})
        unticked = [k for k in trim.get("unticked", []) if k != key]
        if not enabled:
            unticked.append(key)
        trim["unticked"] = unticked
        self._after_edit()

    # -- undo ----------------------------------------------------------------------

    @contextmanager
    def batch(self):
        """Several edits that undo as one."""
        if self._batch == 0:
            self._before_edit()
        self._batch += 1
        try:
            yield
        finally:
            self._batch -= 1

    def _snapshot(self) -> dict[str, Any]:
        # deep copy: to_dict() shares nested dicts (match, settings) with the live file
        return copy.deepcopy(self.mf.to_dict())

    def _before_edit(self) -> None:
        if self._batch:
            return
        self._undo.append(self._snapshot())
        del self._undo[:-MAX_UNDO]
        self._redo.clear()

    def _after_edit(self) -> None:
        self.dirty = True
        self._analysis = None

    def _restore(self, data: dict[str, Any]) -> None:
        seq = self.mf.next_event_seq
        self.mf = MatchFile.from_dict(data)
        self.mf.next_event_seq = max(seq, self.mf.next_event_seq)  # ids are never reused
        self._after_edit()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True

    # -- edits -----------------------------------------------------------------------

    def _check_unlocked(self) -> None:
        if self.locked:
            raise LockedError("events are locked")

    def add(self, t_ms: int, type: str, **fields: Any) -> Event:
        """New event (allowed while locked: the lock freezes existing events)."""
        self._before_edit()
        e = self.mf.add_event(max(0, int(t_ms)), type, **fields)
        self._after_edit()
        return e

    def delete(self, event_id: str) -> Event:
        self._check_unlocked()
        self._before_edit()
        e = self.mf.remove_event(event_id)
        self._after_edit()
        return e

    def move(self, event_id: str, t_ms: int) -> Event:
        self._check_unlocked()
        e = self.mf.event(event_id)
        self._before_edit()
        e.t_ms = max(0, int(t_ms))
        self._after_edit()
        return e

    def update(self, event_id: str, **fields: Any) -> Event:
        self._check_unlocked()
        e = self.mf.event(event_id)
        self._before_edit()
        for key, value in fields.items():
            if not hasattr(e, key) or key in ("id", "extra"):
                raise AttributeError(key)
            setattr(e, key, value)
        self._after_edit()
        return e

    def rename_player(self, old: str, new: str) -> int:
        snapshot = self._snapshot()
        self._before_edit()
        try:
            count = names.rename_player(self.mf, old, new)
        except ValueError:
            self._restore(snapshot)
            if not self._batch:
                self._undo.pop()
            raise
        self._after_edit()
        return count

    def set_match(self, **fields: Any) -> None:
        """Change match settings (kind, format, ...)."""
        self._before_edit()
        self.mf.match.update(fields)
        self._after_edit()

    # -- marking --------------------------------------------------------------------------

    def game_start_server(self, t_ms: int, other_server: bool = False) -> tuple[str, str | None]:
        """(side, player) a Game start at ``t_ms`` records: the predicted server, or the other
        side's. The player is None in doubles when it cannot be predicted (team's choice)."""
        flow = self.flow_at(t_ms)
        match = self.mf.match
        side, player = flow.next_server, flow.next_server_player
        if side is None:
            side, player = "A", None  # unknown: assume ours; Shift+G for the other side
        if other_server:
            side, player = other(side), None
        if player is None and match.get("kind") != "doubles":
            player = names.players(match, side)[0]
        return side, player

    def mark(self, action_id: str, t_ms: int, **extra: Any) -> Event:
        """Log the event an action stands for at ``t_ms``, filling in who serves."""
        action = ACTIONS_BY_ID[action_id]
        if action.event_type is None:
            raise ValueError(f"{action_id} does not log an event")
        if action.event_type in SHOT_TYPES and action.side is not None and not self.locked:
            point = self.shot_modifier_target(t_ms, action.event_type, action.side)
            if point is not None:
                return self.add_shot_to_point(point.id, action.event_type, action.side)
        fields: dict[str, Any] = {}
        if action.result is not None:
            fields["result"] = action.result
        if action.side is not None:
            fields["side"] = action.side
            if self.mf.match.get("kind") != "doubles":
                fields["player"] = names.players(self.mf.match, action.side)[0]
        flow = self.flow_at(t_ms)
        match = self.mf.match
        if action.event_type == catalog.GAME_START:
            side, player = self.game_start_server(t_ms, action.variant == "other_server")
            fields.update(side=side, player=player)
        elif action.event_type in (catalog.SERVE_IN, catalog.FAULT, catalog.LET, catalog.ACE):
            side = point_server_side(flow.score)
            if side is not None:
                fields["side"] = side
                if match.get("kind") != "doubles":
                    fields["player"] = names.players(match, side)[0]
        fields.update(extra)
        return self.add(t_ms, action.event_type, **fields)

    def shot_modifier_target(self, t_ms: int, shot: str, hitter: str) -> Event | None:
        """The Point a shot pressed at ``t_ms`` describes: the last point mark before it is a
        Point at most the modifier window earlier, and its winner (if entered) agrees with
        the shot. None means the shot ends a new point."""
        window = (self.config.get("scoring.shot_modifier_window_ms") if self.config
                  else SHOT_MODIFIER_WINDOW_MS)
        marks = [e for e in self.mf.events if e.type in _POINT_MARKS and e.t_ms <= t_ms]
        if not marks:
            return None
        last = max(marks, key=lambda e: e.t_ms)
        if last.type != catalog.POINT or t_ms - last.t_ms > window:
            return None
        if last.result in ("A", "B") and last.result != shot_point_winner(shot, hitter):
            return None
        return last

    def add_shot_to_point(self, event_id: str, shot: str, hitter: str) -> Event:
        """Record how a Point ended (replacing an earlier shot); sets an unknown winner."""
        e = self.mf.event(event_id)
        details = {**e.details, "shot": shot, "shot_side": hitter}
        fields: dict[str, Any] = {"details": details}
        if e.result not in ("A", "B"):
            fields["result"] = shot_point_winner(shot, hitter)
        return self.update(event_id, **fields)

    # -- saving ------------------------------------------------------------------------------

    def save(self) -> None:
        matchfile.save(self.mf, self.path)
        self.dirty = False
