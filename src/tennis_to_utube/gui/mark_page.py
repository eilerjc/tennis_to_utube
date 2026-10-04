"""Step 2 — Mark: video, transport, score and event buttons, timeline, lists (DESIGN.md §10)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QHBoxLayout, QInputDialog, QLabel, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from .. import catalog, matchfile, names, playback, timeline_view
from ..config import Config
from ..matchfile import MatchFile
from ..session import LockedError, Session
from ..shortcuts import ACTIONS, Shortcuts
from .actions import build_actions, call, key_text
from .event_panel import EndingStateDialog, EventButtons, RulesDialog, ScorePanel, ScoreStateDialog
from .player import PlayerBase, create_player
from .timeline_bar import TimelineBar

AUTOSAVE_DELAY_MS = 1500


def _button(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space must never "click" a focused button
    if tip:
        b.setToolTip(tip)
    return b


class TransportBar(QWidget):
    """Skip/frame/play buttons, speed buttons (no keys, agreed) and the time."""

    def __init__(self, speeds: list[float], parent: QWidget | None = None):
        super().__init__(parent)
        self.buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for action_id, text in (("skip_back", "◀ 5s"), ("skip_back_short", "◀ 1s"),
                                ("frame_back", "◀ frame"), ("play_pause", "Play / Pause"),
                                ("frame_forward", "frame ▶"), ("skip_forward_short", "1s ▶"),
                                ("skip_forward", "5s ▶")):
            b = _button(text)
            self.buttons[action_id] = b
            layout.addWidget(b)
        layout.addSpacing(16)
        self.speed_group = QButtonGroup(self)
        self.speed_group.setExclusive(True)
        self.speed_buttons: dict[float, QPushButton] = {}
        for s in speeds:
            b = _button(f"{s:g}×", "Playback speed")
            b.setCheckable(True)
            b.setFixedWidth(52)
            self.speed_group.addButton(b)
            self.speed_buttons[s] = b
            layout.addWidget(b)
        layout.addStretch(1)
        self.time = QLabel("0:00:00.000")
        self.time.setStyleSheet("font-family: monospace; font-size: 15pt")
        layout.addWidget(self.time)

    def show_keys(self, shortcuts: Shortcuts) -> None:
        for action_id, b in self.buttons.items():
            keys = shortcuts.keys_for(action_id)
            base = b.text().split("  [")[0]
            b.setText(f"{base}  [{key_text(keys[0])}]" if keys else base)

    def show_speed(self, speed: float) -> None:
        for s, b in self.speed_buttons.items():
            b.setChecked(abs(s - speed) < 1e-6)


class MarkPage(QWidget):
    edited = Signal()  # the match changed (events, names, format, ...)
    message = Signal(str)  # for the status bar

    def __init__(self, config: Config, shortcuts: Shortcuts,
                 player_factory: Callable[[QWidget | None], PlayerBase] = create_player,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.shortcuts = shortcuts
        self.session: Session | None = None
        self.selected_id: str | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(AUTOSAVE_DELAY_MS)
        self._autosave.timeout.connect(self.save)

        self.player = player_factory(self)
        speeds = [float(s) for s in config.get("playback.speeds")]
        self.transport = TransportBar(speeds)
        self.transport.show_keys(shortcuts)

        video_col = QVBoxLayout()
        video_col.addWidget(self.player, 1)
        video_col.addWidget(self.transport)
        left = QWidget()
        left.setLayout(video_col)

        # Right: score and event buttons. Bottom: timeline and lists (later steps).
        self.score_panel = ScorePanel()
        self.side_panel = QWidget()
        self.side_layout = QVBoxLayout(self.side_panel)
        self.side_layout.addWidget(self.score_panel)
        self.bottom = QWidget()
        self.bottom_layout = QVBoxLayout(self.bottom)
        self.bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.overview = TimelineBar(zoomable=False)
        self.detail = TimelineBar(zoomable=True)
        self.follow = QCheckBox("Follow playhead")
        self.follow.setChecked(True)
        self.follow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hint = QLabel("Drag: scrub · click a mark: select · Ctrl+drag: move a mark · wheel: zoom")
        hint.setStyleSheet("color: #757575")
        bar_row = QHBoxLayout()
        bar_row.addWidget(self.follow)
        bar_row.addStretch(1)
        bar_row.addWidget(hint)
        self.bottom_layout.addWidget(self.overview)
        self.bottom_layout.addWidget(self.detail)
        self.bottom_layout.addLayout(bar_row)
        for bar in (self.overview, self.detail):
            bar.seekRequested.connect(self._seek_from_bar)
            bar.eventClicked.connect(self.select_event)
            bar.eventMoved.connect(self.move_event)
            bar.message.connect(self.message)
        self.detail.viewChanged.connect(self._update_window_marker)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(left)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setWidget(self.side_panel)
        side_scroll.setMinimumWidth(540)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        top.addWidget(side_scroll)
        top.setStretchFactor(0, 1)
        outer = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(top)
        outer.addWidget(self.bottom)
        outer.setStretchFactor(0, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(outer)

        skip_long = int(config.get("playback.skip_long_ms"))
        skip_short = int(config.get("playback.skip_short_ms"))
        handlers = {
            "play_pause": self.player.toggle_pause,
            "skip_back": lambda: self.player.skip(-skip_long),
            "skip_forward": lambda: self.player.skip(skip_long),
            "skip_back_short": lambda: self.player.skip(-skip_short),
            "skip_forward_short": lambda: self.player.skip(skip_short),
            "frame_back": lambda: self.player.step(-1),
            "frame_forward": lambda: self.player.step(1),
            "prev_event": lambda: self.jump_event(forward=False),
            "next_event": lambda: self.jump_event(forward=True),
            "undo": self.undo,
            "redo": self.redo,
            "delete_event": self.delete_selected,
            "nudge_back": lambda: self.nudge_selected(-1),
            "nudge_forward": lambda: self.nudge_selected(1),
            "lock_events": self.toggle_lock,
            "score_state": self.mark_score_state,
            "note": self.mark_note,
            "rules_change": self.mark_rules_change,
            "ending_state": self.mark_ending_state,
        }
        for a in ACTIONS:
            if a.event_type is not None and a.id not in handlers:
                handlers[a.id] = lambda a=a: self.mark(a.id)
        self.actions = build_actions(self, shortcuts, handlers)
        self.buttons = EventButtons(shortcuts, handlers)
        self.side_layout.addWidget(self.buttons)
        for action_id, b in self.transport.buttons.items():
            b.clicked.connect(call(handlers[action_id]))
        for s, b in self.transport.speed_buttons.items():
            b.clicked.connect(lambda _c=False, s=s: self.player.set_speed(s))
        self.player.positionChanged.connect(self._on_position)
        self.player.speedChanged.connect(self.transport.show_speed)
        self.transport.show_speed(1.0)

    # -- match -------------------------------------------------------------------

    @property
    def match(self) -> MatchFile | None:
        return self.session.mf if self.session else None

    def load_match(self, mf: MatchFile, path: Path) -> None:
        self.save()
        self.session = Session(mf, path, self.config)
        self.selected_id = None
        paths = [matchfile.resolve_source_path(s.path, path) for s in mf.sources]
        fps = mf.sources[0].fps if mf.sources else None
        self.player.load(paths, [s.duration_ms for s in mf.sources], fps)
        self.player.set_speed(1.0)
        self.buttons.set_names(names.short_side_names(
            mf.match, int(self.config.get("names.short_first_letters"))))
        self.refresh()
        self.setFocus()

    def position(self) -> int:
        return self.player.position_ms()

    def _on_position(self, t_ms: int) -> None:
        total = self.player.duration_ms
        self.transport.time.setText(f"{playback.clock_text(t_ms)} / {playback.clock_text(total)}")
        self._show_flow(t_ms)
        self.overview.set_position(t_ms)
        self.detail.set_position(t_ms, follow=self.follow.isChecked())

    def _update_window_marker(self) -> None:
        v = self.detail.view
        self.overview.window_marker = (v.start_ms, v.start_ms + v.span_ms)
        self.overview.update()

    def _seek_from_bar(self, t_ms: int, precise: bool) -> None:
        self.player.seek(t_ms, precise)

    def select_event(self, event_id: str) -> None:
        if self.session is None:
            return
        self.selected_id = event_id
        self.player.set_paused(True)
        self.player.seek(self.session.event(event_id).t_ms)
        self._refresh_bars()

    def move_event(self, event_id: str, t_ms: int) -> None:
        if self.session is None:
            return
        self.selected_id = event_id
        self._edit(lambda: self.session.move(event_id, t_ms), f"Moved to {playback.clock_text(t_ms)}")

    def _refresh_bars(self) -> None:
        if self.session is None:
            return
        mf, total = self.session.mf, self.session.total_ms
        bands = timeline_view.bands(mf.events, total)
        cuts = [(c.start_ms, c.end_ms) for c in self.session.cuts() if c.enabled]
        for bar in (self.overview, self.detail):
            bar.selected = self.selected_id
            bar.locked = self.session.locked
            bar.set_data(mf.events, bands, cuts, self.session.analysis.issues, total)
        self._update_window_marker()

    def _show_flow(self, t_ms: int) -> None:
        if self.session is None:
            return
        flow = self.session.flow_at(t_ms)
        self.score_panel.show_flow(flow, self.session.mf.match)
        self.buttons.set_suggested(flow.suggested())

    def refresh(self) -> None:
        """Redraw everything that depends on the events."""
        self._show_flow(self.position())
        self._refresh_bars()
        locked = self.session is not None and self.session.locked
        self.buttons.buttons["lock_events"].setText(
            ("Unlock events" if locked else "Lock events") + "\n[" +
            key_text((self.shortcuts.keys_for("lock_events") or ("",))[0]) + "]")

    # -- editing -------------------------------------------------------------------

    def _after_edit(self, text: str) -> None:
        self.refresh()
        self.edited.emit()
        self.message.emit(text)
        self._autosave.start()

    def save(self) -> None:
        if self.session is not None and self.session.dirty:
            try:
                self.session.save()
            except OSError as exc:
                self.message.emit(f"Could not save {self.session.path.name}: {exc}")

    def mark(self, action_id: str, **extra) -> None:
        if self.session is None:
            return
        t = playback.mark_time(self.position(), not self.player.is_paused(), self.player.speed(),
                               int(self.config.get("playback.reaction_offset_ms")))
        e = self.session.mark(action_id, t, **extra)
        self.selected_id = e.id
        self._after_edit(f"{catalog.label(e.type)} at {playback.clock_text(t)}")

    # Dialog hooks (tests replace these)
    def ask_note(self) -> str | None:
        text, ok = QInputDialog.getText(self, "Note", "Note:")
        return text if ok and text.strip() else None

    def ask_details(self, dialog) -> dict | None:
        return dialog.details() if dialog.exec() else None

    def mark_note(self) -> None:
        if self.session is not None and (text := self.ask_note()) is not None:
            self.mark("note", note=text)

    def mark_score_state(self) -> None:
        if self.session is None:
            return
        view = self.session.flow_at(self.position()).score
        details = self.ask_details(ScoreStateDialog(self.session.mf.match, bool(view.in_tiebreak), self))
        if details:
            self.mark("score_state", details=details)

    def mark_rules_change(self) -> None:
        if self.session is not None:
            details = self.ask_details(RulesDialog(self.session.mf.match.get("format"), self))
            if details:
                self.mark("rules_change", details=details)

    def mark_ending_state(self) -> None:
        if self.session is not None:
            details = self.ask_details(EndingStateDialog(self))
            if details:
                self.mark("ending_state", details=details)

    def _edit(self, fn, text: str) -> None:
        try:
            fn()
        except LockedError:
            self.message.emit("Events are locked (Lock events to unlock)")
            return
        except KeyError:
            self.selected_id = None
            self.message.emit("No event selected")
            return
        self._after_edit(text)

    def delete_selected(self) -> None:
        if self.session is None or self.selected_id is None:
            self.message.emit("No event selected")
            return
        e = self.session.event(self.selected_id)
        self._edit(lambda: self.session.delete(e.id), f"Deleted {catalog.label(e.type)}")
        if self.session and all(x.id != e.id for x in self.session.mf.events):
            self.selected_id = None

    def nudge_selected(self, frames: int) -> None:
        if self.session is None or self.selected_id is None:
            self.message.emit("No event selected")
            return
        e = self.session.event(self.selected_id)
        t = round(e.t_ms + frames * self.player.frame)
        self._edit(lambda: self.session.move(e.id, t), f"Moved to {playback.clock_text(t)}")
        if not self.session.locked:
            self.player.seek(self.session.event(e.id).t_ms)

    def toggle_lock(self) -> None:
        if self.session is not None:
            self.session.locked = not self.session.locked
            self.refresh()
            self.message.emit("Events locked" if self.session.locked else "Events unlocked")

    def undo(self) -> None:
        if self.session is not None and self.session.undo():
            self._after_edit("Undone")

    def redo(self) -> None:
        if self.session is not None and self.session.redo():
            self._after_edit("Redone")

    # -- navigation --------------------------------------------------------------

    def jump_event(self, forward: bool) -> None:
        if self.match is None:
            return
        t = playback.neighbor_event([e.t_ms for e in self.match.events], self.position(),
                                    self.player.frame, forward)
        if t is not None:
            self.player.set_paused(True)
            self.player.seek(t)
            self.selected_id = next(e.id for e in self.match.sorted_events() if e.t_ms == t)
            self._refresh_bars()

    def shutdown(self) -> None:
        self._autosave.stop()
        self.save()
        self.player.shutdown()
