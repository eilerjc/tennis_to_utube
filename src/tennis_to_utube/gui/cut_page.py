"""Cut only (DESIGN.md §12b): play the videos, mark the parts to remove (I … O), make the
video. One shot: nothing is saved but the video."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import playback
from ..config import Config
from ..cutlist import CutList, Made, default_output, make_video
from ..probe import MediaInfo, Tools
from ..shortcuts import Shortcuts
from .actions import build_actions, call, key_text
from .mark_page import TransportBar, _button
from .player import PlayerBase, create_player
from .timeline_bar import TimelineBar
from .worker import Job


class CutPage(QWidget):
    message = Signal(str)  # for the status bar

    def __init__(self, config: Config, shortcuts: Shortcuts,
                 player_factory: Callable[[QWidget | None], PlayerBase] = create_player,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.shortcuts = shortcuts
        self.infos: list[MediaInfo] = []
        self.cut_list = CutList(0)
        self.made_removals: list[tuple[int, int]] | None = None  # what the last video removed
        self.job: Job | None = None
        self._cancel: threading.Event | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.player = player_factory(self)
        self.transport = TransportBar([float(s) for s in config.get("playback.speeds")])
        self.transport.show_keys(shortcuts)
        video_col = QVBoxLayout()
        video_col.addWidget(self.player, 1)
        video_col.addWidget(self.transport)
        left = QWidget()
        left.setLayout(video_col)

        # Right: the removals and making the video
        self.files_label = QLabel()
        self.files_label.setWordWrap(True)
        self.start_button = _button("Start removal", "Mark where a part to remove begins")
        self.end_button = _button("End removal", "Mark where it ends (then it is removed)")
        self.delete_button = _button("Delete removal")
        self.undo_button = _button("Undo")
        self.redo_button = _button("Redo")
        mark_row = QHBoxLayout()
        for b in (self.start_button, self.end_button):
            b.setMinimumHeight(56)
            mark_row.addWidget(b)
        edit_row = QHBoxLayout()
        for b in (self.delete_button, self.undo_button, self.redo_button):
            edit_row.addWidget(b)
        self.pending_label = QLabel()
        self.pending_label.setStyleSheet("color: #b35c00")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Remove from", "to", "Length"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys stay with the video
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.summary = QLabel()
        removals = QGroupBox("Parts to remove")
        r_layout = QVBoxLayout(removals)
        r_layout.addLayout(mark_row)
        r_layout.addWidget(self.pending_label)
        r_layout.addWidget(self.table, 1)
        r_layout.addLayout(edit_row)
        r_layout.addWidget(self.summary)

        self.output_edit = QLineEdit()
        self.browse_button = QPushButton("Browse…")
        self.make_button = QPushButton("Make video")
        self.make_button.setMinimumHeight(48)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.browse_button)
        make_row = QHBoxLayout()
        make_row.addWidget(self.make_button, 1)
        make_row.addWidget(self.cancel_button)
        output = QGroupBox("Make video (lossless: no re-encoding)")
        o_layout = QVBoxLayout(output)
        o_layout.addWidget(QLabel("Save as"))
        o_layout.addLayout(out_row)
        o_layout.addLayout(make_row)
        o_layout.addWidget(self.progress)
        o_layout.addWidget(self.status)

        side = QWidget()
        side.setMinimumWidth(480)
        side_col = QVBoxLayout(side)
        side_col.setContentsMargins(0, 0, 0, 0)
        side_col.addWidget(self.files_label)
        side_col.addWidget(removals, 1)
        side_col.addWidget(output)

        self.overview = TimelineBar(zoomable=False)
        self.detail = TimelineBar(zoomable=True)
        self.follow = QCheckBox("Follow playhead")
        self.follow.setChecked(True)
        self.follow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hint = QLabel("Shaded: removed · drag: scrub · wheel: zoom · I … O: remove that part")
        hint.setStyleSheet("color: #757575")
        bar_row = QHBoxLayout()
        bar_row.addWidget(self.follow)
        bar_row.addStretch(1)
        bar_row.addWidget(hint)
        bottom = QWidget()
        b_layout = QVBoxLayout(bottom)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.addWidget(self.overview)
        b_layout.addWidget(self.detail)
        b_layout.addLayout(bar_row)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(left)
        top.addWidget(side)
        top.setStretchFactor(0, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(top, 1)
        layout.addWidget(bottom)

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
            "prev_event": lambda: self.jump_edge(forward=False),
            "next_event": lambda: self.jump_edge(forward=True),
            "undo": self.undo,
            "redo": self.redo,
            "delete_event": self.delete_removal,
            "cut_start": self.start_removal,
            "cut_end": self.end_removal,
        }
        self.actions = build_actions(self, shortcuts, handlers)
        for action_id, b in self.transport.buttons.items():
            b.clicked.connect(call(handlers[action_id]))
        for action_id, b in (("cut_start", self.start_button), ("cut_end", self.end_button),
                             ("delete_event", self.delete_button), ("undo", self.undo_button),
                             ("redo", self.redo_button)):
            keys = shortcuts.keys_for(action_id)
            if keys:
                b.setText(f"{b.text()}\n[{key_text(keys[0])}]")
            b.clicked.connect(call(handlers[action_id]))
        self.transport.speed.activated.connect(
            lambda i: self.player.set_speed(self.transport.speed.itemData(i)))
        self.player.positionChanged.connect(self._on_position)
        self.player.speedChanged.connect(self.transport.show_speed)
        self.transport.show_speed(1.0)
        for bar in (self.overview, self.detail):
            bar.seekRequested.connect(lambda t, precise: self.player.seek(t, precise))
        self.detail.viewChanged.connect(self._update_window_marker)
        self.table.cellClicked.connect(self._on_row_clicked)
        self.browse_button.clicked.connect(self._browse)
        self.make_button.clicked.connect(self.make)
        self.cancel_button.clicked.connect(self.cancel)

    # -- videos ------------------------------------------------------------------------

    def load(self, infos: Sequence[MediaInfo]) -> None:
        self.infos = list(infos)
        total = sum(i.duration_ms for i in self.infos)
        self.cut_list = CutList(total)
        self.made_removals = None
        self.player.load([i.path for i in self.infos], [i.duration_ms for i in self.infos],
                         self.infos[0].fps if self.infos else None)
        self.player.set_speed(1.0)
        names = ", ".join(i.path.name for i in self.infos)
        self.files_label.setText(f"<b>{len(self.infos)} video{'s' * (len(self.infos) != 1)}</b>"
                                 f" joined in order: {names} ({playback.clock_text(total)})")
        self.output_edit.setText(str(default_output(self.infos[0])) if self.infos else "")
        self.status.setText("Mark the parts to remove, or make the video as it is to just "
                            "join the files.")
        self.refresh()
        self.setFocus()

    def position(self) -> int:
        return self.player.position_ms()

    def _on_position(self, t_ms: int) -> None:
        total = self.player.duration_ms
        self.transport.time.setText(f"{playback.clock_text(t_ms)} / {playback.clock_text(total)}")
        self.overview.set_position(t_ms)
        self.detail.set_position(t_ms, follow=self.follow.isChecked())
        if self.cut_list.pending_start is not None:
            self._refresh_bars()

    def _update_window_marker(self) -> None:
        v = self.detail.view
        self.overview.window_marker = (v.start_ms, v.start_ms + v.span_ms)
        self.overview.update()

    # -- removals ----------------------------------------------------------------------

    def start_removal(self) -> None:
        if not self.infos:
            return
        self.cut_list.start(self.position())
        self.refresh()
        self.message.emit(f"Removal starts at {playback.clock_text(self.cut_list.pending_start)}"
                          " — go to where it ends and press End removal")

    def end_removal(self) -> None:
        if not self.infos:
            return
        from_start = self.cut_list.pending_start is None  # no start: from the beginning
        added = self.cut_list.end(self.position())
        self.refresh()
        if added is None:
            self.message.emit("Nothing removed: the end is where it started")
        elif from_start:
            self.message.emit(f"Removing the beginning, up to {playback.clock_text(added[1])}")
        else:
            self.message.emit(f"Removing {playback.clock_text(added[0])} – "
                              f"{playback.clock_text(added[1])}")

    def _selected_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            return rows[0].row()
        return self.cut_list.index_at(self.position())

    def delete_removal(self) -> None:
        if self.cut_list.pending_start is not None:
            self.cut_list.pending_start = None
            self.refresh()
            self.message.emit("Removal start cleared")
            return
        i = self._selected_index()
        if i is None:
            self.message.emit("Select a removal (or stand inside one) to delete it")
            return
        s, e = self.cut_list.delete(i)
        self.refresh()
        self.message.emit(f"Kept {playback.clock_text(s)} – {playback.clock_text(e)} again")

    def undo(self) -> None:
        if self.cut_list.undo():
            self.refresh()
            self.message.emit("Undone")

    def redo(self) -> None:
        if self.cut_list.redo():
            self.refresh()
            self.message.emit("Redone")

    def jump_edge(self, forward: bool) -> None:
        """Previous/next removal start or end."""
        edges = sorted({t for r in self.cut_list.removals for t in r})
        t = playback.neighbor_event(edges, self.position(), self.player.frame, forward)
        if t is not None:
            self.player.set_paused(True)
            self.player.seek(t)

    def _on_row_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.cut_list.removals):
            self.player.set_paused(True)
            self.player.seek(self.cut_list.removals[row][0])
            self.setFocus()

    def _bar_cuts(self) -> list[tuple[int, int]]:
        cuts = list(self.cut_list.removals)
        start = self.cut_list.pending_start
        if start is not None:
            a, b = sorted((start, self.position()))
            cuts.append((a, max(b, a + 1)))
        return cuts

    def _refresh_bars(self) -> None:
        for bar in (self.overview, self.detail):
            bar.set_data([], [], self._bar_cuts(), [], self.cut_list.total_ms)
        self._update_window_marker()

    def refresh(self) -> None:
        cl = self.cut_list
        selected = self._selected_index() if self.table.selectionModel().selectedRows() else None
        self.table.setRowCount(len(cl.removals))
        for row, (s, e) in enumerate(cl.removals):
            for col, text in enumerate((playback.clock_text(s), playback.clock_text(e),
                                        playback.clock_text(e - s))):
                self.table.setItem(row, col, QTableWidgetItem(text))
        if selected is not None and selected < len(cl.removals):
            self.table.selectRow(selected)
        else:
            self.table.clearSelection()
        self.pending_label.setText(
            "" if cl.pending_start is None else
            f"Removal starts at {playback.clock_text(cl.pending_start)} — press End removal "
            "where it ends (Delete clears it)")
        self.summary.setText(f"Keeping {playback.clock_text(cl.kept_ms)} of "
                             f"{playback.clock_text(cl.total_ms)}"
                             + (f" — {len(cl.removals)} part{'s' * (len(cl.removals) != 1)} removed"
                                if cl.removals else ""))
        self._refresh_bars()

    # -- making the video --------------------------------------------------------------

    def has_unmade_work(self) -> bool:
        """Removals marked that no video was made with (they are not saved anywhere)."""
        if self.cut_list.pending_start is not None:
            return True
        return bool(self.cut_list.removals) and self.cut_list.removals != self.made_removals

    def confirm_discard(self) -> bool:
        """Ask before throwing away removals that were not made into a video."""
        if not self.has_unmade_work():
            return True
        answer = QMessageBox.question(
            self, "Discard the removals?",
            "The parts you marked are not saved anywhere and no video was made with them. "
            "Discard them?")
        return answer == QMessageBox.StandardButton.Yes

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save video as", self.output_edit.text(),
                                              "MP4 video (*.mp4)")
        if path:
            self.output_edit.setText(path)

    def confirm_overwrite(self, path: Path) -> bool:
        answer = QMessageBox.question(self, "Replace video?", f"{path.name} exists. Replace it?")
        return answer == QMessageBox.StandardButton.Yes

    def make(self) -> None:
        if not self.infos or self.job is not None:
            return
        text = self.output_edit.text().strip()
        if not text:
            self.message.emit("Choose where to save the video")
            return
        output = Path(text)
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
        if output.resolve() in {i.path.resolve() for i in self.infos}:
            self.status.setText("That is one of the source videos; choose another name.")
            return
        if output.exists() and not self.confirm_overwrite(output):
            return
        if self.cut_list.pending_start is not None:
            self.status.setText("A removal was started but not ended: press End removal, or "
                                "Delete to clear it.")
            return
        infos, cut_list = list(self.infos), self.cut_list
        removals = list(cut_list.removals)
        tools = Tools.from_config(self.config)
        cancel = threading.Event()

        def work(report):
            return make_video(infos, cut_list, output, tools, report, cancel)

        self._cancel = cancel
        self.job = Job(work, self)
        self.job.progressed.connect(self._on_progress)
        self.job.succeeded.connect(lambda made: self._on_done(made, removals))
        self.job.failed.connect(self._on_failed)
        self._set_busy(True)
        self.player.set_paused(True)
        self.job.start()

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.status.setText("Cancelling…")

    def _set_busy(self, busy: bool) -> None:
        self.make_button.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.output_edit.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0)

    def _on_progress(self, fraction: float, text: str) -> None:
        if fraction < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(fraction * 1000))
        self.status.setText(text)

    def _finish_job(self) -> None:
        if self.job is not None:
            self.job.wait()
        self.job = None
        self._cancel = None
        self._set_busy(False)

    def _on_done(self, made: Made, removals: list[tuple[int, int]]) -> None:
        self._finish_job()
        self.made_removals = removals
        problems = [i.message for i in made.result.issues if i.severity != "info"]
        self.status.setText(made.summary() + ("\n" + "\n".join(problems) if problems else ""))
        self.message.emit(made.summary())

    def _on_failed(self, error: str) -> None:
        self._finish_job()
        self.status.setText("Cancelled." if error == "cancelled" else f"Could not make the video: {error}")

    def shutdown(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        if self.job is not None:
            self.job.wait()
        self.player.shutdown()
