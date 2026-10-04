"""Step 2 — Mark: video, transport, score and event buttons, timeline, lists (DESIGN.md §10)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSplitter, QStyle, QVBoxLayout, QWidget,
)

from .. import catalog, matchfile, names, playback, timeline_view
from ..config import Config
from ..matchfile import MatchFile
from ..session import LockedError, Session
from ..shortcuts import ACTIONS, ACTIONS_BY_ID, Shortcuts
from .actions import build_actions, call, key_text
from .event_panel import EndingStateDialog, EventButtons, RulesDialog, ScorePanel, ScoreStateDialog
from .lists import EventDialog, ListsPanel, MatchDialog
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
    """Skip/frame/play buttons, a speed drop-down (no keys, agreed) and the time."""

    def __init__(self, speeds: list[float], parent: QWidget | None = None):
        super().__init__(parent)
        self.buttons: dict[str, QPushButton] = {}
        self.labels: dict[str, str] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Direction is shown with the style's media icons (text arrows like ◀ render badly
        # in some Windows fonts); forward buttons have the icon on the right.
        sp = QStyle.StandardPixmap
        for action_id, text, icon, forward in (
                ("skip_back", "5 s", sp.SP_MediaSeekBackward, False),
                ("skip_back_short", "1 s", sp.SP_MediaSeekBackward, False),
                ("frame_back", "Frame", sp.SP_MediaSkipBackward, False),
                ("play_pause", "Play / Pause", sp.SP_MediaPlay, False),
                ("frame_forward", "Frame", sp.SP_MediaSkipForward, True),
                ("skip_forward_short", "1 s", sp.SP_MediaSeekForward, True),
                ("skip_forward", "5 s", sp.SP_MediaSeekForward, True)):
            b = _button(text)
            b.setIcon(self.style().standardIcon(icon))
            b.setIconSize(QSize(20, 20))
            b.setMinimumWidth(96)
            if forward:
                b.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.buttons[action_id] = b
            self.labels[action_id] = text
            layout.addWidget(b)
        layout.addSpacing(16)
        layout.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys stay with the video
        self.speed.setToolTip("Playback speed")
        for sp in speeds:
            self.speed.addItem(f"{sp:g}×", sp)
        self.speed.setMinimumHeight(44)
        layout.addWidget(self.speed)
        layout.addStretch(1)
        self.time = QLabel("0:00:00.000")
        self.time.setStyleSheet("font-family: monospace; font-size: 15pt")
        layout.addWidget(self.time)

    def show_keys(self, shortcuts: Shortcuts) -> None:
        """Two lines per button: what it does, and its key underneath."""
        for action_id, b in self.buttons.items():
            keys = shortcuts.keys_for(action_id)
            b.setText(f"{self.labels[action_id]}\n[{key_text(keys[0])}]" if keys else self.labels[action_id])

    def show_speed(self, speed: float) -> None:
        i = next((i for i in range(self.speed.count())
                  if abs(self.speed.itemData(i) - speed) < 1e-6), -1)
        if i < 0:  # a speed not in the list (set elsewhere): show it anyway
            self.speed.addItem(f"{speed:g}×", speed)
            i = self.speed.count() - 1
        self.speed.blockSignals(True)
        self.speed.setCurrentIndex(i)
        self.speed.blockSignals(False)


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
        self.match_button = _button("Players && format…", "Rename players, change the format")
        self.match_button.clicked.connect(lambda *_: self.edit_match())
        self.side_panel = QWidget()  # the button groups (scroll); the score stays on top
        self.side_layout = QVBoxLayout(self.side_panel)
        head = QHBoxLayout()
        head.addWidget(self.score_panel, 1)
        head.addWidget(self.match_button, 0, Qt.AlignmentFlag.AlignTop)
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
        self.lists = ListsPanel()
        self.bottom_layout.addWidget(self.overview)
        self.bottom_layout.addWidget(self.detail)
        self.bottom_layout.addLayout(bar_row)
        self.bottom_layout.addWidget(self.lists, 1)
        self.lists.eventActivated.connect(self.select_event)
        self.lists.eventEditRequested.connect(self.edit_event)
        self.lists.timeActivated.connect(lambda t: self.player.seek(t))
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
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side = QWidget()
        side.setMinimumWidth(540)
        side_col = QVBoxLayout(side)
        side_col.setContentsMargins(0, 0, 0, 0)
        side_col.addLayout(head)
        side_col.addWidget(side_scroll, 1)
        top.addWidget(side)
        top.setStretchFactor(0, 1)
        outer = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(top)
        outer.addWidget(self.bottom)
        outer.setStretchFactor(0, 3)
        outer.setStretchFactor(1, 1)
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
            "game_start": lambda: self.mark_game_start(other_server=False),
            "game_start_other_server": lambda: self.mark_game_start(other_server=True),
            "set_server": self.set_server,
        }
        for a in ACTIONS:
            if a.event_type is not None and a.id not in handlers:
                handlers[a.id] = lambda a=a: self.mark(a.id)
        self.actions = build_actions(self, shortcuts, handlers)
        self.buttons = EventButtons(shortcuts, handlers)
        self.side_layout.addWidget(self.buttons)
        for action_id, b in self.transport.buttons.items():
            b.clicked.connect(call(handlers[action_id]))
        self.transport.speed.activated.connect(
            lambda i: self.player.set_speed(self.transport.speed.itemData(i)))
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
        self.lists.select_event(event_id)

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
        self.lists.set_data(mf.events, self.session.analysis, mf.match, self.session.analysis.issues)
        self.lists.select_event(self.selected_id)

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

    def external_change(self, text: str = "") -> None:
        """Another step changed the match through the shared session."""
        self.refresh()
        self.edited.emit()
        self._autosave.start()
        if text:
            self.message.emit(text)

    def show_time(self, t_ms: int) -> None:
        self.player.set_paused(True)
        self.player.seek(t_ms)

    def save(self) -> None:
        if self.session is not None and self.session.dirty:
            try:
                self.session.save()
            except OSError as exc:
                self.message.emit(f"Could not save {self.session.path.name}: {exc}")

    def mark_time(self) -> int:
        return playback.mark_time(self.position(), not self.player.is_paused(), self.player.speed(),
                                  int(self.config.get("playback.reaction_offset_ms")))

    def mark(self, action_id: str, t: int | None = None, **extra) -> None:
        if self.session is None:
            return
        t = self.mark_time() if t is None else t
        e = self.session.mark(action_id, t, **extra)
        self.selected_id = e.id
        action_type = ACTIONS_BY_ID[action_id].event_type
        if action_type != e.type:  # a shot added to the Point just marked
            self._after_edit(f"{catalog.label(action_type)} added to the Point at "
                             f"{playback.clock_text(e.t_ms)}")
        else:
            self._after_edit(f"{catalog.label(e.type)} at {playback.clock_text(t)}")

    def _all_players(self) -> list[str]:
        match = self.session.mf.match
        return names.players(match, "A") + names.players(match, "B")

    def mark_game_start(self, other_server: bool) -> None:
        """Game start with the predicted server. When the server is not known yet (first
        game), or in doubles when the team's server cannot be predicted, a quick picker
        (keys 1/2/…) — the time is taken at the key press. Esc leaves the server open."""
        if self.session is None:
            return
        t = self.mark_time()
        side, player = self.session.game_start_server(t, other_server)
        if side is None:
            player = self.ask_server(self._all_players())
            side = names.side_of(self.session.mf.match, player) if player else None
        elif player is None:
            player = self.ask_server(names.players(self.session.mf.match, side))
        action = "game_start_other_server" if other_server else "game_start"
        self.mark(action, t, side=side, player=player)

    def set_server(self) -> None:
        """Set server…: pick who serves now (changes the current game's Game start, or
        records the server for the next game)."""
        if self.session is None:
            return
        t = self.mark_time()
        player = self.ask_server(self._all_players())
        if player is None:
            return

        def apply():
            self.selected_id = self.session.set_server(t, player).id

        self._edit(apply, f"Server: {player}")

    # Dialog hooks (tests replace these)
    def ask_server(self, players: list[str]) -> str | None:
        """Pick the server from a small menu at the mouse (1/2 or arrows + Enter)."""
        menu = QMenu(self)
        menu.addSection("Who serves?")
        for i, name in enumerate(players, start=1):
            act = menu.addAction(f"&{i}  {name.replace('&', '&&')}")
            act.setData(name)
        chosen = menu.exec(QCursor.pos())
        return chosen.data() if chosen is not None else None
    def ask_note(self) -> str | None:
        text, ok = QInputDialog.getText(self, "Note", "Note:")
        return text if ok and text.strip() else None

    def run_dialog(self, dialog) -> bool:
        return bool(dialog.exec())

    def ask_details(self, dialog) -> dict | None:
        return dialog.details() if self.run_dialog(dialog) else None

    def edit_event(self, event_id: str) -> None:
        if self.session is None:
            return
        if self.session.locked:
            self.message.emit("Events are locked (Lock events to unlock)")
            return
        e = self.session.event(event_id)
        dialog = EventDialog(e, self.session.mf.match, self)
        if self.run_dialog(dialog):
            self.selected_id = event_id
            self._edit(lambda: self.session.update(event_id, **dialog.changes()),
                       f"Edited {catalog.label(e.type)}")

    def edit_match(self) -> None:
        if self.session is None:
            return
        dialog = MatchDialog(self.session.mf.match, self)
        if not self.run_dialog(dialog):
            return
        renames = dialog.renames()
        count = 0
        with self.session.batch():  # one undo step
            # via temporary names, so swaps (A↔B) never collide
            temps = [(old, f"\u0000{i}") for i, (old, _) in enumerate(renames)]
            for old, tmp in temps:
                self.session.rename_player(old, tmp)
            for (_, tmp), (_, new) in zip(temps, renames):
                count += self.session.rename_player(tmp, new)
            if dialog.format_spec() != (self.session.mf.match.get("format") or {}):
                self.session.set_match(format=dialog.format_spec())
        self.buttons.set_names(names.short_side_names(
            self.session.mf.match, int(self.config.get("names.short_first_letters"))))
        self._after_edit("Match updated")
        if renames:
            self.inform("Players renamed",
                        f"Replaced {count} occurrence(s) in {self.session.path.name}.")

    def inform(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def mark_note(self) -> None:
        if self.session is not None and (text := self.ask_note()) is not None:
            self.mark("note", note=text)

    def mark_score_state(self) -> None:
        if self.session is None:
            return
        t = self.position()
        view = self.session.flow_at(t).score
        details = self.ask_details(ScoreStateDialog(self.session.mf.match, bool(view.in_tiebreak), self,
                                                    server=self.session.server_player_at(t)))
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

    def _selected_event(self):
        """The selected event, or None (with a message) — it may be gone after an undo."""
        if self.session is not None and self.selected_id is not None:
            try:
                return self.session.event(self.selected_id)
            except KeyError:
                self.selected_id = None
        self.message.emit("No event selected")
        return None

    def delete_selected(self) -> None:
        e = self._selected_event()
        if e is None:
            return
        self._edit(lambda: self.session.delete(e.id), f"Deleted {catalog.label(e.type)}")
        if self.session and all(x.id != e.id for x in self.session.mf.events):
            self.selected_id = None

    def nudge_selected(self, frames: int) -> None:
        e = self._selected_event()
        if e is None:
            return
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
