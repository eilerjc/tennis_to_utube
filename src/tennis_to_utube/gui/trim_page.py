"""Step 3 — Trim: choose removal rules, untick cuts, make the video (DESIGN.md §9).

The cuts come from the match and ``trim.toml`` (re-read whenever the step is shown).
"Check exact cuts" finds the keyframes with ffprobe here; **Make video runs the Trim tool**
(:mod:`tennis_to_utube.trimtool`) as its own process, showing its progress, with Cancel.
The tool also writes the trimmed video's chapters and links; after uploading, its YouTube
link is pasted here and the links are rewritten (the full recording's are on Export).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from .. import playback
from ..config import Config, load_trim_config
from ..issues import Issue
from ..probe import ProbeKeyframes, Tools
from ..session import Session
from ..timeline import Interval, merge_intervals
from ..trim import RULE_LABELS, RULES, TrimPlan, remap_events
from ..trimtool import command, default_output, export_paths
from ..youtube import parse_video_id
from .worker import Job


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
        self.job: Job | None = None  # "Check exact cuts" (in this process)
        self.process: QProcess | None = None  # "Make video" / links (the Trim tool)
        self._out_buffer = ""
        self._result: dict | None = None
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
        self.config_note = QLabel()
        self.config_note.setStyleSheet("color: #757575")
        rules_row.addWidget(self.config_note)

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
        self.cancel_button.clicked.connect(lambda *_: self.cancel())
        buttons = QHBoxLayout()
        for b in (self.plan_button, self.make_button, self.cancel_button):
            buttons.addWidget(b)
        buttons.addWidget(self.progress, 1)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # The trimmed video on YouTube: its own id, chapters and links (from the Trim tool)
        self.video = QLineEdit()
        self.video.setPlaceholderText("After uploading the trimmed video: paste its YouTube link or id")
        self.video.editingFinished.connect(self._video_entered)
        self.video_note = QLabel()
        self.chapters = QPlainTextEdit()
        self.chapters.setReadOnly(True)
        self.chapters.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.chapters.setMaximumHeight(160)
        self.copy_button = QPushButton("Copy chapters")
        self.copy_button.clicked.connect(lambda *_: self.copy_chapters())
        self.files_note = QLabel()
        self.files_note.setWordWrap(True)
        self.files_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        trimmed = QGroupBox("Trimmed video on YouTube")
        t_layout = QVBoxLayout(trimmed)
        vid_row = QHBoxLayout()
        vid_row.addWidget(self.video, 1)
        vid_row.addWidget(self.video_note)
        t_layout.addLayout(vid_row)
        ch_row = QHBoxLayout()
        ch_row.addWidget(self.chapters, 1)
        ch_row.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        t_layout.addLayout(ch_row)
        t_layout.addWidget(self.files_note)
        self.trimmed_box = trimmed

        layout = QVBoxLayout(self)
        layout.addLayout(rules_row)
        layout.addWidget(QLabel("Untick a cut to keep that footage. Double-click a cut to see it."))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.summary)
        layout.addLayout(out_row)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addWidget(trimmed)

    # -- data ----------------------------------------------------------------------

    def load(self, session: Session) -> None:
        self.session = session
        self.plan = None
        session.trim_config = load_trim_config()
        out = session.output_path() or default_output(session.path, session.trim_config)
        self.output.setText(str(out))
        self.result.setText("")
        self.refresh()

    def refresh(self) -> None:
        """Rules, cuts and summary from the match and trim.toml (call when the page is shown)."""
        if self.session is None:
            return
        self.session.trim_config = load_trim_config()  # edits to trim.toml apply right away
        warnings = self.session.trim_config.warnings
        self.config_note.setText("trim.toml: " + "; ".join(warnings) if warnings else "")
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
        self._show_trimmed()

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

    def _show_trimmed(self) -> None:
        """The trimmed video's YouTube id and chapters (enabled once a video was made)."""
        assert self.session is not None
        output = self.session.mf.output or {}
        self.trimmed_box.setEnabled(bool(output))
        self.video.setText(output.get("video_id") or "")
        chapters_path, md_path, csv_path = export_paths(self.session.path)
        try:
            self.chapters.setPlainText(chapters_path.read_text(encoding="utf-8") if output else "")
        except OSError:
            self.chapters.setPlainText("")
        if not output:
            self.video_note.setText("make the video first")
            self.files_note.setText("")
            return
        self.video_note.setText("" if output.get("video_id") else "links will have times only")
        self.files_note.setText(f"Links: {md_path.name}, {csv_path.name} (next to the match file). "
                                "The full recording's chapters and links are on the Export step.")

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

    # -- check exact cuts (in this process) --------------------------------------------

    def _tools(self) -> Tools:
        return Tools.from_config(self.config)

    def _keyframes(self) -> ProbeKeyframes:
        assert self.session is not None
        return ProbeKeyframes(self.session.source_paths(), self._tools(),
                              durations_ms=[s.duration_ms for s in self.session.mf.sources])

    def busy(self) -> bool:
        return ((self.job is not None and self.job.isRunning())
                or (self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning))

    def _busy(self, busy: bool) -> None:
        for w in (self.plan_button, self.make_button, self.choose, self.output, self.table,
                  self.trimmed_box, *self.rules.values()):
            w.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if not busy and self.session is not None:
            self.trimmed_box.setEnabled(bool(self.session.mf.output))

    def _on_progress(self, fraction: float, text: str) -> None:
        if fraction < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(fraction * 1000))
        self.progress.setFormat(text)

    def _stopped(self, text: str) -> None:
        self._busy(False)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("")
        self.result.setText(text)

    def start_plan(self) -> None:
        if self.session is None or self.busy():
            return
        session = self.session

        def work(report):
            report(-1, "Finding keyframes…")
            return session.plan(self._keyframes())

        def done(plan: TrimPlan) -> None:
            self._busy(False)
            self.plan = plan
            self.progress.setFormat("")
            _, issues = remap_events(session.mf.events, plan)
            self._show_summary()
            self._show_issues(issues)

        self._cancel.clear()
        self._busy(True)
        self.job = Job(work, self)
        self.job.progressed.connect(self._on_progress)
        self.job.succeeded.connect(done)
        self.job.failed.connect(lambda text: self._stopped(
            "Cancelled." if text == "cancelled" else f"<b>Failed:</b> {text}"))
        self.job.start()

    # -- the Trim tool (its own process) -------------------------------------------------

    def _run_tool(self, args: list[str], on_done) -> None:
        """Run the Trim tool on the saved match file; ``on_done(exit_code, result)``."""
        assert self.session is not None
        self.session.save()  # the tool reads the match file
        argv, env = command(self.session.path, "--from-gui", *args)
        proc = QProcess(self)
        q_env = QProcessEnvironment()
        for k, v in env.items():
            q_env.insert(k, v)
        proc.setProcessEnvironment(q_env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._out_buffer, self._result = "", None
        proc.readyReadStandardOutput.connect(lambda: self._read_tool(proc))
        proc.finished.connect(lambda code, _status: self._tool_finished(proc, code, on_done))
        proc.errorOccurred.connect(lambda err: err == QProcess.ProcessError.FailedToStart
                                   and self._stopped("<b>Failed:</b> could not start the Trim tool"))
        self.process = proc
        self._busy(True)
        proc.start(argv[0], argv[1:])

    def _read_tool(self, proc: QProcess) -> None:
        self._out_buffer += bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._out_buffer = self._out_buffer.split("\n")
        for line in lines:
            line = line.rstrip("\r")
            if line.startswith("PROGRESS "):
                _, fraction, *text = line.split(" ", 2)
                self._on_progress(float(fraction), text[0] if text else "")
            elif line.startswith("RESULT "):
                self._result = json.loads(line[len("RESULT "):])

    def _tool_finished(self, proc: QProcess, code: int, on_done) -> None:
        self._read_tool(proc)
        if self._out_buffer:
            self._out_buffer += "\n"
            self._read_tool(proc)
        errors = bytes(proc.readAllStandardError()).decode("utf-8", "replace").strip()
        self.process = None
        if code == 2:
            self._stopped("Cancelled.")
        elif code != 0 or self._result is None:
            self._stopped(f"<b>Failed:</b> {errors.splitlines()[-1] if errors else f'exit code {code}'}")
        else:
            self._busy(False)
            on_done(self._result)
        proc.deleteLater()

    def cancel(self) -> None:
        self._cancel.set()
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            if self.process.state() == QProcess.ProcessState.Starting:
                self.process.waitForStarted(5_000)
            self.process.write(b"cancel\n")

    def start_make(self) -> None:
        if self.session is None or self.busy():
            return
        output = Path(self.output.text().strip())
        if not output.name:
            self.result.setText("Choose a file name for the video.")
            return
        self._on_progress(-1, "Starting the Trim tool…")
        self._run_tool(["-o", str(output)], self._made)

    def _made(self, result: dict) -> None:
        assert self.session is not None
        self.session.set_output(result["output"])
        self.session.save()
        self.plan = self.session.output_plan()
        self.progress.setFormat("Done")
        issues = [Issue(i["code"], i["message"], i.get("t_ms"), severity=i.get("severity", "warning"))
                  for i in result.get("issues", [])]
        self._show_summary()
        self._show_issues(issues, made=Path(result["video"]))
        self._show_trimmed()
        self.changed.emit()
        self.message.emit(f"Made {Path(result['video']).name}")

    def _video_entered(self) -> None:
        if self.session is None or not self.session.mf.output or self.busy():
            return
        text = self.video.text().strip()
        vid = parse_video_id(text) if text else None
        if text and vid is None:
            self.video_note.setText("<span style='color:#c62828'>not a YouTube link or id</span>")
            return
        if vid == self.session.mf.output.get("video_id"):
            return
        self.session.set_output_video_id(vid)
        self.changed.emit()
        self._run_tool(["--links-only"], lambda _r: (self._show_trimmed(),
                                                       self.message.emit("Trimmed video's links rewritten")))

    def copy_chapters(self) -> None:
        text = self.chapters.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.message.emit("Chapters copied")

    def _show_issues(self, issues, made: Path | None = None) -> None:
        lines = []
        if made is not None:
            lines.append(f"<b>Made {made.name}</b> and checked it with ffprobe; its chapters "
                         "and links are below.")
        notable = [i for i in issues if i.severity != "info"]
        removed = [i for i in issues if i.code == "event_in_removed_region"]
        if removed:
            lines.append(f"{len(removed)} event(s) are inside removed footage and will not be "
                         "in the trimmed video's links (see Issues on the Mark step):")
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
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.write(b"cancel\n")
            if not self.process.waitForFinished(10_000):
                self.process.kill()
