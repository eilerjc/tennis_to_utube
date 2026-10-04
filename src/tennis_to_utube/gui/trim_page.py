"""Step 3 — Trim: choose removal rules, untick cuts, make the video (DESIGN.md §9).

Planning finds the exact keyframes with ffprobe; making the video runs ffmpeg (stream copy)
with progress and Cancel, then checks the result with ffprobe. The produced video's plan is
stored in the match file so the export can remap event times.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import playback
from ..config import Config
from ..probe import ProbeKeyframes, Tools
from ..session import Session
from ..timeline import Interval, merge_intervals
from ..trim import RULE_LABELS, RULES, TrimPlan, plan_to_dict, remap_events, run_trim
from .worker import Job


def default_output(match_path: Path) -> Path:
    stem = match_path.name.removesuffix(".match.json")
    return match_path.with_name(f"{stem} trimmed.mp4")


def _clock(ms: int) -> str:
    return playback.clock_text(ms).split(".")[0]


class TrimPage(QWidget):
    showTime = Signal(int)  # jump to a time on the Mark step
    changed = Signal()  # the match changed (rules, cuts, output)
    message = Signal(str)

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.session: Session | None = None
        self.plan: TrimPlan | None = None
        self.job: Job | None = None
        self._cancel = threading.Event()
        self._filling = False

        rules_row = QHBoxLayout()
        rules_row.addWidget(QLabel("Remove:"))
        self.rules: dict[str, QCheckBox] = {}
        for rule in RULES:
            box = QCheckBox(RULE_LABELS[rule])
            box.toggled.connect(self._rules_changed)
            self.rules[rule] = box
            rules_row.addWidget(box)
        rules_row.addStretch(1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Cut", "What", "From", "To", "Length"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._show_cut)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.output = QLineEdit()
        self.choose = QPushButton("Choose…")
        self.choose.clicked.connect(self._choose_output)
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Video file:"))
        out_row.addWidget(self.output, 1)
        out_row.addWidget(self.choose)

        self.plan_button = QPushButton("Check exact cuts")
        self.make_button = QPushButton("Make video")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        self.plan_button.clicked.connect(lambda *_: self.start_plan())
        self.make_button.clicked.connect(lambda *_: self.start_make())
        self.cancel_button.clicked.connect(lambda *_: self._cancel.set())
        buttons = QHBoxLayout()
        for b in (self.plan_button, self.make_button, self.cancel_button):
            buttons.addWidget(b)
        buttons.addWidget(self.progress, 1)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addLayout(rules_row)
        layout.addWidget(QLabel("Untick a cut to keep that footage. Double-click a cut to see it."))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.summary)
        layout.addLayout(out_row)
        layout.addLayout(buttons)
        layout.addWidget(self.result)

    # -- data ----------------------------------------------------------------------

    def load(self, session: Session) -> None:
        self.session = session
        self.plan = None
        out = session.output_path() or default_output(session.path)
        self.output.setText(str(out))
        self.result.setText("")
        self.refresh()

    def refresh(self) -> None:
        """Rules, cuts and summary from the match (call when the page is shown)."""
        if self.session is None:
            return
        self._filling = True
        chosen = set(self.session.trim_rules())
        for rule, box in self.rules.items():
            box.setChecked(rule in chosen)
        cuts = self.session.cuts()
        self.table.setRowCount(len(cuts))
        for row, c in enumerate(cuts):
            use = QTableWidgetItem("remove")
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked if c.enabled else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, c.key)
            self.table.setItem(row, 0, use)
            for col, text in enumerate((c.label, _clock(c.start_ms), _clock(c.end_ms),
                                        _clock(c.end_ms - c.start_ms)), start=1):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, c.start_ms)
                self.table.setItem(row, col, item)
        self._filling = False
        self._show_summary()

    def _show_summary(self) -> None:
        assert self.session is not None
        total = self.session.total_ms
        removed = sum(iv.length for iv in merge_intervals(
            Interval(c.start_ms, c.end_ms) for c in self.session.cuts() if c.enabled))
        text = (f"Recording {_clock(total)}; the ticked cuts remove about {_clock(removed)}, "
                f"leaving about {_clock(total - removed)}. Cuts start and end on nearby "
                "keyframes, so a little more is kept (never less).")
        if self.plan is not None:
            text += (f"<br><b>Exact:</b> {len(self.plan.segments)} kept part(s), "
                     f"video length {_clock(self.plan.total_out_ms)}.")
        if self.session.mf.output:
            made = sorted(self.session.mf.output.get("cuts", []))
            now = sorted(c.key for c in self.session.cuts() if c.enabled)
            if made != now:
                text += "<br><span style='color:#ef6c00'>The cuts changed since the video was made.</span>"
        self.summary.setText(text)

    def _rules_changed(self) -> None:
        if self._filling or self.session is None:
            return
        self.session.set_trim_rules([r for r, b in self.rules.items() if b.isChecked()])
        self.plan = None
        self.refresh()
        self.changed.emit()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or self.session is None or item.column() != 0:
            return
        self.session.set_cut_enabled(item.data(Qt.ItemDataRole.UserRole),
                                     item.checkState() == Qt.CheckState.Checked)
        self.plan = None
        self._show_summary()
        self.changed.emit()

    def _show_cut(self, row: int, _col: int) -> None:
        item = self.table.item(row, 1)
        if item is not None:
            self.showTime.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _choose_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Video file", self.output.text(), "MP4 video (*.mp4)")
        if path:
            self.output.setText(path)

    # -- jobs ----------------------------------------------------------------------

    def _tools(self) -> Tools:
        return Tools.from_config(self.config)

    def _keyframes(self) -> ProbeKeyframes:
        assert self.session is not None
        return ProbeKeyframes(self.session.source_paths(), self._tools(),
                              durations_ms=[s.duration_ms for s in self.session.mf.sources])

    def _busy(self, busy: bool) -> None:
        for w in (self.plan_button, self.make_button, self.choose, self.output, self.table,
                  *self.rules.values()):
            w.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def _run(self, fn, done) -> None:
        self._cancel.clear()
        self._busy(True)
        self.job = Job(fn, self)
        self.job.progressed.connect(self._on_progress)
        self.job.succeeded.connect(lambda r: (self._busy(False), done(r)))
        self.job.failed.connect(self._on_failed)
        self.job.start()

    def _on_progress(self, fraction: float, text: str) -> None:
        if fraction < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(fraction * 1000))
        self.progress.setFormat(text)

    def _on_failed(self, text: str) -> None:
        self._busy(False)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("")
        self.result.setText("Cancelled." if text == "cancelled" else f"<b>Failed:</b> {text}")

    def start_plan(self) -> None:
        if self.session is None:
            return
        session = self.session

        def work(report):
            report(-1, "Finding keyframes…")
            return session.plan(self._keyframes())

        def done(plan: TrimPlan) -> None:
            self.plan = plan
            self.progress.setFormat("")
            _, issues = remap_events(session.mf.events, plan)
            self._show_summary()
            self._show_issues(issues)

        self._run(work, done)

    def start_make(self) -> None:
        if self.session is None:
            return
        session = self.session
        output = Path(self.output.text().strip())
        if not output.name:
            self.result.setText("Choose a file name for the video.")
            return
        paths = session.source_paths()
        cancel = self._cancel

        def work(report):
            report(-1, "Finding keyframes…")
            plan = session.plan(self._keyframes())
            report(0.0, "Writing video…")
            result = run_trim(plan, paths, output, self._tools(), cancel=cancel,
                              progress=lambda f: report(f, f"Writing video… {f:.0%}"))
            report(1.0, "Checked")
            return result

        def done(result) -> None:
            self.plan = result.plan
            try:
                stored = os.path.relpath(result.output, session.path.parent)
            except ValueError:  # another drive (Windows)
                stored = str(result.output)
            session.set_output(plan_to_dict(result.plan, Path(stored).as_posix()))
            session.save()
            self._show_summary()
            _, issues = remap_events(session.mf.events, result.plan)
            self._show_issues(list(result.issues) + issues, made=result.output)
            self.changed.emit()
            self.message.emit(f"Made {result.output.name}")

        self._run(work, done)

    def _show_issues(self, issues, made: Path | None = None) -> None:
        lines = []
        if made is not None:
            lines.append(f"<b>Made {made.name}</b> and checked it with ffprobe.")
        notable = [i for i in issues if i.severity != "info"]
        removed = [i for i in issues if i.code == "event_in_removed_region"]
        if removed:
            lines.append(f"{len(removed)} event(s) are inside removed footage and will not be "
                         "exported (see Issues on the Mark step):")
            lines += [f"&nbsp;&nbsp;{_clock(i.t_ms)} — {i.message}" for i in removed[:10]]
        for i in notable:
            if i.code != "event_in_removed_region":
                lines.append(f"{i.severity}: {i.message}")
        if not lines:
            lines.append("No problems found.")
        self.result.setText("<br>".join(lines))

    def shutdown(self) -> None:
        if self.job is not None and self.job.isRunning():
            self._cancel.set()
            self.job.wait(10_000)
