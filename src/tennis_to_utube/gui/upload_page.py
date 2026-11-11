"""Upload (DESIGN.md §12c): the YouTube account, the upload queue with its dashboard, and
diagnostics. The work is done by :class:`..uploadqueue.Manager` on its own threads; this
page only shows :meth:`Manager.snapshot` twice a second and passes on what is clicked.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import uploadpresets
from ..appstate import AppState
from ..config import Config, load_upload_config
from ..uploadqueue import (
    ACTIVE, CATEGORIES, COMPLETE, FAILED, PAUSED, PRIVACY, PROCESSING, QUEUED, UPLOADED, Manager,
    QueueBusy, Store, check_details, new_job, setup_log,
)
from ..uploadtool import default_details, duration_text, size_text, speed_text
from ..ytapi import ApiError, AuthError, NetworkError
from .worker import Job

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.mts);;All files (*)"
STATE_KEY = "upload"  # state.json: {"parallel": n, "prevent_sleep": bool}

_STATUS_COLORS = {FAILED: "#c62828", COMPLETE: "#2e7d32", PAUSED: "#757575",
                  "Reconnecting": "#ef6c00", "Resuming": "#ef6c00"}

_FOUR_K_NOTE = ("YouTube makes the low resolutions first: HD and 4K (2160p) versions can take "
                "hours longer to appear, and YouTube does not tell apps when they are ready — "
                "check the quality menu on the video.")


class UploadDialog(QDialog):
    """Title, description and settings for the videos about to be queued."""

    PLAIN, GAME, MATCH = "plain", "game", "match"

    def __init__(self, paths: Sequence[Path], defaults: dict[str, Any],
                 playlists: Sequence[dict[str, str]] = (), preset: str = PLAIN,
                 texts: Sequence[dict[str, str]] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Upload to YouTube")
        self.setMinimumWidth(820)
        self.paths = [Path(p) for p in paths]
        self._texts = list(texts) if texts else None
        self._edited: set[int] = set()  # titles typed by hand are not regenerated
        self._filling = False

        self.preset = QComboBox()
        self.preset.addItem("File names", self.PLAIN)
        self.preset.addItem("Game: Team A vs Team B - First Half, Second Half …", self.GAME)
        if self._texts:
            self.preset.addItem("Tennis match (from the match file)", self.MATCH)
        self.team_a = QLineEdit()
        self.team_a.setPlaceholderText("Team A")
        self.team_b = QLineEdit()
        self.team_b.setPlaceholderText("Team B")
        self.date = QLineEdit(uploadpresets.file_date(self.paths[0]) if self.paths else "")
        self.date.setMaximumWidth(140)
        game_row = QHBoxLayout()
        game_row.setContentsMargins(0, 0, 0, 0)
        for label, edit in (("Teams", self.team_a), ("vs", self.team_b), ("Date", self.date)):
            game_row.addWidget(QLabel(label))
            game_row.addWidget(edit, 0 if edit is self.date else 1)
        self.game_box = QWidget()
        self.game_box.setLayout(game_row)

        self.table = QTableWidget(len(self.paths), 3)
        self.table.setHorizontalHeaderLabels(["File", "Part", "Title (editable)"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(60 + 34 * max(1, min(len(self.paths), 6)))
        self.parts: list[QComboBox] = []
        for row, (path, part) in enumerate(zip(self.paths, uploadpresets.game_parts(len(self.paths)))):
            name = QTableWidgetItem(path.name)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(uploadpresets.GAME_PARTS)
            combo.setCurrentText(part)
            combo.currentTextChanged.connect(lambda *_: self._fill_titles())
            self.table.setCellWidget(row, 1, combo)
            self.parts.append(combo)
            self.table.setItem(row, 2, QTableWidgetItem(""))

        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(150)
        self.description_label = QLabel("Description")
        self.tags = QLineEdit(", ".join(defaults.get("tags", [])))
        self.tags.setPlaceholderText("comma separated (optional)")
        self.privacy = QComboBox()
        for p in PRIVACY:
            self.privacy.addItem(p.capitalize(), p)
        self.privacy.setCurrentIndex(max(0, self.privacy.findData(defaults.get("privacy"))))
        self.category = QComboBox()
        for cid, name in CATEGORIES.items():
            self.category.addItem(name, cid)
        self.category.setCurrentIndex(max(0, self.category.findData(defaults.get("category"))))
        self.playlist = QComboBox()
        self.playlist.addItem("(none)", "")
        for p in playlists:
            self.playlist.addItem(p["title"], p["id"])
        wanted = defaults.get("playlist") or ""
        if wanted and self.playlist.findData(wanted) < 0:
            self.playlist.addItem(wanted, wanted)
        self.playlist.setCurrentIndex(max(0, self.playlist.findData(wanted)))
        self.kids_yes = QRadioButton("Yes, it is made for kids")
        self.kids_no = QRadioButton("No, it is not made for kids")
        self.kids = QButtonGroup(self)
        self.kids.addButton(self.kids_yes)
        self.kids.addButton(self.kids_no)
        if defaults.get("made_for_kids") is not None:
            (self.kids_yes if defaults["made_for_kids"] else self.kids_no).setChecked(True)
        kids_row = QHBoxLayout()
        kids_row.addWidget(self.kids_no)
        kids_row.addWidget(self.kids_yes)
        kids_row.addStretch(1)

        form = QFormLayout()
        form.addRow("Name the videos by", self.preset)
        form.addRow("", self.game_box)
        form.addRow(self.table)
        form.addRow(self.description_label, self.description)
        form.addRow("Tags", self.tags)
        form.addRow("Privacy", self.privacy)
        form.addRow("Category", self.category)
        form.addRow("Playlist", self.playlist)
        form.addRow("Made for kids?", kids_row)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #c62828")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add to the upload queue")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("The video file is uploaded as it is: nothing is re-encoded or "
                                "reduced. YouTube asks whether a video is made for kids; this "
                                "app does not guess."))
        layout.addWidget(self.problems)
        layout.addWidget(self.buttons)

        self.preset.setCurrentIndex(max(0, self.preset.findData(
            self.MATCH if self._texts else preset)))
        self.preset.currentIndexChanged.connect(lambda *_: self._preset_changed())
        for edit in (self.team_a, self.team_b, self.date):
            edit.textChanged.connect(lambda *_: self._fill_titles())
        self.table.itemChanged.connect(self._title_typed)
        self.description.textChanged.connect(self._check)
        self.tags.textChanged.connect(lambda *_: self._check())
        self.kids.buttonToggled.connect(lambda *_: self._check())
        self._preset_changed()

    def mode(self) -> str:
        return self.preset.currentData()

    def _preset_changed(self) -> None:
        game = self.mode() == self.GAME
        self.game_box.setVisible(game)
        self.table.setColumnHidden(1, not game)
        self.description_label.setText("Notes (added to each\ndescription)" if game else "Description")
        self._edited.clear()
        if self.mode() == self.MATCH and self._texts:
            self.description.setPlainText(self._texts[0].get("description", ""))
        elif self._texts and self.description.toPlainText() == self._texts[0].get("description", ""):
            self.description.setPlainText("")
        self._fill_titles()

    def _fill_titles(self) -> None:
        self._filling = True
        for row, path in enumerate(self.paths):
            if row in self._edited:
                continue
            if self.mode() == self.GAME:
                title = uploadpresets.game_title(self.team_a.text(), self.team_b.text(),
                                                 self.parts[row].currentText())
            elif self.mode() == self.MATCH and self._texts and row < len(self._texts):
                title = self._texts[row]["title"]
            else:
                title = uploadpresets.plain_title(path)
            self.table.item(row, 2).setText(title)
        self._filling = False
        self._check()

    def _title_typed(self, item: QTableWidgetItem) -> None:
        if not self._filling and item.column() == 2:
            self._edited.add(item.row())
            self._check()

    def made_for_kids(self) -> bool | None:
        if self.kids_yes.isChecked():
            return True
        return False if self.kids_no.isChecked() else None

    def details(self) -> list[dict[str, Any]]:
        """One set of job details per file, as they stand."""
        tags = [t.strip() for t in self.tags.text().split(",") if t.strip()]
        text = self.description.toPlainText().strip()
        out = []
        for row in range(len(self.paths)):
            description = text
            if self.mode() == self.GAME:
                description = uploadpresets.game_description(
                    self.team_a.text(), self.team_b.text(), self.date.text(),
                    self.parts[row].currentText(), text)
            out.append({"title": self.table.item(row, 2).text().strip(),
                        "description": description, "tags": tags,
                        "privacy": self.privacy.currentData(),
                        "category": self.category.currentData(),
                        "playlist": self.playlist.currentData() or "",
                        "made_for_kids": bool(self.made_for_kids())})
        return out

    def _check(self) -> None:
        problems: list[str] = []
        for d in self.details():
            for p in check_details(d["title"], d["description"], d["tags"]):
                if p not in problems:
                    problems.append(p)
        if self.made_for_kids() is None:
            problems.append("Choose whether the videos are made for kids.")
        self.problems.setText(" ".join(problems))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problems)


class UploadPage(QWidget):
    message = Signal(str)
    # A job's video id is known and it belongs to a match: (job row). The main window puts
    # the link into the match and answers with :meth:`linked`.
    videoReady = Signal(dict)

    COLUMNS = ("File", "Title", "Size", "Done", "Speed", "Average", "Time left", "Status",
               "Last checkpoint", "YouTube link")

    def __init__(self, state: AppState | None = None, upload_config: Config | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state or AppState()
        self.config = upload_config or load_upload_config()
        self.manager: Manager | None = None
        self.rows: list[dict[str, Any]] = []
        self.channel: dict[str, str] | None = None
        self.playlists: list[dict[str, str]] = []
        self._job: Job | None = None
        self._cancel_connect: threading.Event | None = None
        self._announced: set[tuple[str, str]] = set()
        self._link_asked: dict[str, float] = {}
        self.open_browser = webbrowser.open  # (tests put a stand-in here)
        remembered = self.state.extra.get(STATE_KEY)
        remembered = remembered if isinstance(remembered, dict) else {}

        # The account
        self.account = QLabel()
        self.account.setTextFormat(Qt.TextFormat.RichText)
        self.connect_button = QPushButton("Connect to YouTube…")
        self.disconnect_button = QPushButton("Disconnect")
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 4)
        self.parallel.setToolTip("How many videos upload at the same time. More is not faster "
                                 "if your internet connection is the limit.")
        self.keep_awake = QCheckBox("Keep the PC awake while uploading")
        top = QHBoxLayout()
        top.addWidget(self.account, 1)
        top.addWidget(self.connect_button)
        top.addWidget(self.disconnect_button)
        top.addSpacing(24)
        top.addWidget(QLabel("Uploads at once:"))
        top.addWidget(self.parallel)
        top.addWidget(self.keep_awake)

        # The queue
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.add_button = QPushButton("Add videos…")
        self.game_button = QPushButton("Add a game…")
        self.game_button.setToolTip("Pick the videos of a game: they are named "
                                    "\"Team A vs Team B - First Half\", \"Second Half\", …")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.retry_button = QPushButton("Retry")
        self.restart_button = QPushButton("Restart from the beginning")
        self.remove_button = QPushButton("Remove")
        self.open_button = QPushButton("Open on YouTube")
        self.copy_button = QPushButton("Copy link")
        self.hold_button = QPushButton("Pause all")
        self.hold_button.setCheckable(True)
        buttons = QHBoxLayout()
        for b in (self.add_button, self.game_button):
            buttons.addWidget(b)
        buttons.addSpacing(24)
        for b in (self.pause_button, self.resume_button, self.retry_button, self.restart_button,
                  self.remove_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        for b in (self.open_button, self.copy_button, self.hold_button):
            buttons.addWidget(b)

        self.total = QProgressBar()
        self.total.setRange(0, 1000)
        self.total_label = QLabel()

        # The selected upload
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.diagnostics = QLabel()
        self.diagnostics.setTextFormat(Qt.TextFormat.RichText)
        self.diagnostics.setWordWrap(True)
        self.diagnostics.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.diagnostics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_box = QGroupBox("Selected upload")
        QVBoxLayout(detail_box).addWidget(self.detail)
        diag_box = QGroupBox("Diagnostics: where does the time go?")
        QVBoxLayout(diag_box).addWidget(self.diagnostics)
        lower = QHBoxLayout()
        lower.addWidget(detail_box, 1)
        lower.addWidget(diag_box, 1)
        self.log_note = QLabel()
        self.log_note.setStyleSheet("color: #757575")
        self.log_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        total_row = QHBoxLayout()
        total_row.addWidget(self.total, 1)
        total_row.addWidget(self.total_label)
        layout.addLayout(total_row)
        layout.addLayout(lower)
        layout.addWidget(self.log_note)

        self.connect_button.clicked.connect(lambda *_: self.connect_account())
        self.disconnect_button.clicked.connect(lambda *_: self.disconnect_account())
        self.add_button.clicked.connect(lambda *_: self.pick_videos(UploadDialog.PLAIN))
        self.game_button.clicked.connect(lambda *_: self.pick_videos(UploadDialog.GAME))
        self.pause_button.clicked.connect(lambda *_: self._act("pause"))
        self.resume_button.clicked.connect(lambda *_: self._act("resume"))
        self.retry_button.clicked.connect(lambda *_: self._act("retry"))
        self.restart_button.clicked.connect(lambda *_: self.restart_selected())
        self.remove_button.clicked.connect(lambda *_: self.remove_selected())
        self.open_button.clicked.connect(lambda *_: self.open_selected())
        self.copy_button.clicked.connect(lambda *_: self.copy_link())
        self.hold_button.toggled.connect(self._hold)
        self.table.itemSelectionChanged.connect(self._show_selected)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self._open_queue(remembered)

    # -- the queue's owner -------------------------------------------------------------

    def _open_queue(self, remembered: dict[str, Any]) -> None:
        try:
            log_path = setup_log()
            manager = Manager(self.config, store=Store()).open()
        except QueueBusy as exc:
            self.account.setText(f"<b>{exc}</b> Use that one, or close it and start this app again.")
            for w in (self.connect_button, self.disconnect_button, self.add_button,
                      self.game_button, self.parallel, self.keep_awake, self.hold_button):
                w.setEnabled(False)
            self._enable_buttons(None)
            return
        except OSError as exc:
            self.account.setText(f"<b>The upload queue cannot be opened:</b> {exc}")
            return
        self.manager = manager
        if isinstance(remembered.get("parallel"), int):
            manager.parallel = min(4, max(1, remembered["parallel"]))
        if isinstance(remembered.get("prevent_sleep"), bool):
            manager.prevent_sleep = remembered["prevent_sleep"]
        self.parallel.setValue(min(4, manager.parallel))
        self.keep_awake.setChecked(manager.prevent_sleep)
        self.parallel.valueChanged.connect(self._settings_changed)
        self.keep_awake.toggled.connect(self._settings_changed)
        self.log_note.setText(f"Log: {log_path}")
        if manager.warnings:
            self.message.emit("  |  ".join(manager.warnings))
        manager.start()
        self.timer.start()
        self._show_account()
        if manager.auth.connected:
            self._load_channel()
        self.refresh()

    def _settings_changed(self, *_: Any) -> None:
        if self.manager is None:
            return
        self.manager.parallel = self.parallel.value()
        self.manager.prevent_sleep = self.keep_awake.isChecked()
        self.state.extra[STATE_KEY] = {"parallel": self.manager.parallel,
                                       "prevent_sleep": self.manager.prevent_sleep}

    def uploading(self) -> bool:
        return self.manager is not None and self.manager.uploading()

    def confirm_close(self) -> bool:
        """Ask before closing while videos are uploading."""
        if not self.uploading():
            return True
        answer = QMessageBox.question(
            self, "Uploads are running",
            "Videos are still uploading. Close anyway?\n\nNothing is lost: the next time the "
            "app starts they carry on from their last checkpoint.")
        return answer == QMessageBox.StandardButton.Yes

    def shutdown(self) -> None:
        self.timer.stop()
        if self._cancel_connect is not None:
            self._cancel_connect.set()
        if self._job is not None:
            self._job.wait(3000)
        if self.manager is not None:
            self.manager.close()
            self.manager = None

    # -- the account -------------------------------------------------------------------

    def _show_account(self, note: str = "") -> None:
        if self.manager is None:
            return
        auth = self.manager.auth
        busy = self._cancel_connect is not None
        if not auth.configured:
            text = ("<b>YouTube is not set up yet.</b> Uploading needs your own Google client "
                    "id in upload.toml — see docs/UPLOAD_SETUP.md.")
        elif auth.connected:
            name = self.channel["title"] if self.channel else "YouTube"
            text = f"Connected to <b>{name}</b>"
        else:
            text = "<b>Not connected to YouTube.</b> Queued videos wait until you connect."
        if busy:
            text = "Waiting for the sign-in in your browser…"
        self.account.setText(text + (f" <span style='color:#c62828'>{note}</span>" if note else ""))
        self.connect_button.setEnabled(auth.configured and (busy or not auth.connected))
        self.connect_button.setText("Cancel sign-in" if busy else "Connect to YouTube…")
        self.disconnect_button.setEnabled(auth.connected and not busy)

    def connect_account(self) -> None:
        """Sign in through the browser (in the background; the button becomes Cancel)."""
        if self.manager is None:
            return
        if self._cancel_connect is not None:
            self._cancel_connect.set()
            return
        cancel = threading.Event()
        manager, browser = self.manager, self.open_browser

        def work(report):
            manager.auth.connect(open_browser=browser, cancel=cancel)
            return self._fetch_account(manager)

        self._cancel_connect = cancel
        self._run(work, self._account_loaded,
                  lambda error: self._show_account("" if error == "cancelled" else error))
        self._show_account()

    @staticmethod
    def _fetch_account(manager: Manager) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
        channel = manager.api.channel()
        try:
            playlists = manager.api.playlists()
        except (ApiError, NetworkError):
            playlists = []
        return channel, playlists

    def _load_channel(self) -> None:
        manager = self.manager
        if manager is None or self._job is not None:
            return
        self._run(lambda report: self._fetch_account(manager), self._account_loaded,
                  lambda error: self._show_account(f"(could not ask YouTube which channel: {error})"))

    def _run(self, work, done, failed) -> None:
        job = Job(work, self)

        def finish(handler, value) -> None:
            job.wait()
            self._job = None
            self._cancel_connect = None
            handler(value)

        job.succeeded.connect(lambda value: finish(done, value))
        job.failed.connect(lambda error: finish(failed, error))
        self._job = job
        job.start()

    def _account_loaded(self, result: tuple[dict[str, str] | None, list[dict[str, str]]]) -> None:
        self.channel, self.playlists = result
        note = "" if self.channel else "(this Google account has no YouTube channel)"
        self._show_account(note)
        if self.channel:
            self.message.emit(f"Connected to {self.channel['title']}")

    def disconnect_account(self) -> None:
        if self.manager is None:
            return
        self.manager.auth.disconnect()
        self.channel, self.playlists = None, []
        self._show_account()
        self.message.emit("Disconnected from YouTube (uploads wait until you connect again)")

    # -- adding videos -----------------------------------------------------------------

    def ask(self, dialog: UploadDialog) -> bool:
        """Show the details dialog (tests replace this)."""
        return dialog.exec() == QDialog.DialogCode.Accepted

    def choose_files(self) -> list[Path]:
        start = self.state.last_folder or ""
        names, _ = QFileDialog.getOpenFileNames(self, "Videos to upload", start, VIDEO_FILTER)
        return [Path(n) for n in names]

    def pick_videos(self, preset: str) -> None:
        paths = self.choose_files()
        if paths:
            self.add_videos(sorted(paths, key=lambda p: p.name.lower()), preset=preset)

    def add_videos(self, paths: Sequence[Path], preset: str = UploadDialog.PLAIN,
                   texts: Sequence[dict[str, str]] | None = None,
                   link: dict[str, Any] | None = None) -> list[str]:
        """Ask for the details of ``paths`` and queue them; returns the new job ids.
        ``texts`` proposes a title/description per file (a tennis match); ``link`` says
        which match gets the video id afterwards."""
        if self.manager is None:
            return []
        paths = [Path(p) for p in paths]
        missing = [p.name for p in paths if not p.is_file()]
        if missing or not paths:
            self.message.emit("Not found: " + ", ".join(missing) if missing else "No videos chosen")
            return []
        queued = {(r["path"], r["size"]) for r in self.rows
                  if r["status"] not in (FAILED,) and not r["video_id"]}
        already = [p.name for p in paths
                   if (str(p.resolve()), p.stat().st_size) in queued]
        if already:
            self.message.emit("Already in the upload queue: " + ", ".join(already))
            return []
        dialog = UploadDialog(paths, default_details(self.config), self.playlists, preset, texts,
                              self)
        if not self.ask(dialog):
            return []
        ids = []
        for path, details in zip(paths, dialog.details()):
            try:
                job = new_job(path, link=dict(link) if link else None, **details)
            except (OSError, ValueError) as exc:
                self.message.emit(f"Cannot upload {path.name}: {exc}")
                continue
            ids.append(self.manager.add(job).id)
        self.refresh()
        if ids:
            waits = "" if self.manager.auth.connected else " — connect to YouTube to start"
            self.message.emit(f"{len(ids)} video{'s' * (len(ids) != 1)} added to the upload queue{waits}")
        return ids

    # -- showing the queue -------------------------------------------------------------

    def _selected(self) -> dict[str, Any] | None:
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self.rows):
            return self.rows[rows[0].row()]
        return None

    def refresh(self) -> None:
        if self.manager is None:
            return
        selected = self._selected()
        selected_id = selected["id"] if selected else None
        self.rows = self.manager.snapshot()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            checkpoint = "—"
            if row["checkpoint_at"]:
                checkpoint = (f"{size_text(row['confirmed'])} at "
                              f"{time.strftime('%H:%M:%S', time.localtime(row['checkpoint_at']))}")
            uploading = row["status"] in ACTIVE
            cells = (row["name"], row["title"], size_text(row["size"]), f"{row['percent']:.1f}%",
                     speed_text(row["speed"]) if row["status"] == "Uploading" else "—",
                     speed_text(row["average"]) if row["average"] else "—",
                     duration_text(row["eta_s"]) if uploading else "—",
                     row["status"], checkpoint, row["url"] or "")
            for c, text in enumerate(cells):
                item = self.table.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(r, c, item)
                if item.text() != text:
                    item.setText(text)
                if c == 7:
                    color = _STATUS_COLORS.get(row["status"])
                    item.setData(Qt.ItemDataRole.ForegroundRole, QColor(color) if color else None)
                    item.setToolTip(row["error"] or row["note"])
            if row["id"] == selected_id:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        if selected_id is None and self.rows and not self.table.selectionModel().selectedRows():
            self.table.selectRow(0)
        self._show_totals()
        self._show_selected()
        self._announce()

    def _show_totals(self) -> None:
        open_rows = [r for r in self.rows if not r["video_id"] and r["status"] != FAILED]
        size = sum(r["size"] for r in open_rows)
        done = sum(r["done"] for r in open_rows)
        speed = sum(r["speed"] for r in self.rows)
        self.total.setVisible(bool(open_rows))
        self.total.setValue(int(1000 * done / size) if size else 0)
        if not open_rows:
            self.total_label.setText("")
            return
        left = duration_text((size - done) / speed) if speed > 0 else "—"
        self.total_label.setText(f"{size_text(done)} of {size_text(size)} · {speed_text(speed)} "
                                 f"in total · about {left} left")

    def _enable_buttons(self, row: dict[str, Any] | None) -> None:
        status = row["status"] if row else None
        self.pause_button.setEnabled(status in (*ACTIVE, QUEUED))
        self.resume_button.setEnabled(status == PAUSED)
        self.retry_button.setEnabled(status == FAILED and not row["needs_restart"])
        self.restart_button.setEnabled(status == FAILED and not row["video_id"])
        self.remove_button.setEnabled(row is not None and not row["running"])
        self.open_button.setEnabled(bool(row and row["url"]))
        self.copy_button.setEnabled(bool(row and row["url"]))

    def _show_selected(self) -> None:
        row = self._selected()
        self._enable_buttons(row)
        if row is None:
            self.detail.setText("Add videos to upload them to YouTube. Uploads run in the "
                                "background, also while this window is minimized, and resume "
                                "by themselves after a lost connection or a restart.")
            self.diagnostics.setText("")
            return
        self.detail.setText(self.detail_text(row))
        self.diagnostics.setText(self.diagnostics_text(row))

    @staticmethod
    def detail_text(row: dict[str, Any]) -> str:
        status = row["status"]
        meaning = {
            QUEUED: "Waiting for its turn.",
            PAUSED: "Paused. Resume carries on from the last checkpoint.",
            "Uploading": "Sending the file to YouTube.",
            "Reconnecting": "The connection was lost. Trying again, a little longer apart each "
                            "time; nothing already confirmed is sent again.",
            "Resuming": "Asking YouTube how much of the file it has, to carry on from there.",
            UPLOADED: "YouTube has the whole file. Waiting for it to start processing.",
            PROCESSING: "YouTube has the whole file and is preparing it for viewing. This PC "
                        "is no longer needed for this video.",
            COMPLETE: "YouTube has finished processing: the video can be watched.",
            FAILED: "Stopped; see below.",
        }.get(status, "")
        lines = [f"<b>{row['title']}</b>", f"{row['name']} · {size_text(row['size'])} · "
                 f"{row['privacy']}" + (" · made for kids" if row["made_for_kids"] else ""),
                 f"<b>{status}.</b> {meaning}"]
        if row["error"]:
            lines.append(f"<span style='color:#c62828'>{row['error']}</span>")
            if row["needs_restart"]:
                lines.append("It can only be started again from the beginning (Restart from the "
                             "beginning). That is never done without you asking.")
        if row["note"]:
            lines.append(row["note"])
        if row["url"]:
            lines.append(f"Video: {row['url']}")
        if status in (PROCESSING, COMPLETE):
            lines.append(f"<span style='color:#757575'>{_FOUR_K_NOTE}</span>")
        return "<br>".join(lines)

    @staticmethod
    def diagnostics_text(row: dict[str, Any]) -> str:
        active, lost = row["active_s"], row["reconnect_s"]
        cells = [("Speed now", speed_text(row["speed"]) if row["status"] == "Uploading" else "—"),
                 ("Average speed", speed_text(row["average"]) if row["average"] else "—"),
                 ("Uploading for", duration_text(active)),
                 ("Sent", size_text(row["sent_bytes"])),
                 ("Confirmed by YouTube", size_text(row["size"] if row["video_id"] else row["confirmed"])),
                 ("Remaining", size_text(row["remaining"])),
                 ("Retries", str(row["retries"])),
                 ("Connection interruptions", str(row["interruptions"])),
                 ("Time spent reconnecting", duration_text(lost))]
        table = "<table>" + "".join(f"<tr><td style='padding-right:14px'>{k}</td><td><b>{v}</b></td></tr>"
                                    for k, v in cells) + "</table>"
        return table + "<br>" + UploadPage.verdict(row)

    @staticmethod
    def verdict(row: dict[str, Any]) -> str:
        """A plain reading of the counters: is it the connection dropping, or just its speed?"""
        active, lost = row["active_s"], row["reconnect_s"]
        if active < 20 or not row["sent_bytes"]:
            return "Not enough uploaded yet to say anything."
        resent = max(0, row["sent_bytes"] - (row["size"] if row["video_id"] else row["confirmed"])
                     - (row["done"] - row["confirmed"] if not row["video_id"] else 0))
        if row["interruptions"] and lost > 0.1 * active:
            return (f"The connection dropped {row['interruptions']} time(s) and "
                    f"{100 * lost / active:.0f}% of the time was spent waiting for it to come "
                    "back: the slowdown is interruptions (router, Wi-Fi, provider), not the speed.")
        if resent > 0.05 * row["sent_bytes"]:
            return (f"{size_text(resent)} had to be sent twice after interruptions. The "
                    "connection is unstable.")
        if row["interruptions"]:
            return (f"{row['interruptions']} short interruption(s), little time lost. The rest "
                    "is the upload speed itself: compare the average with an internet speed "
                    "test (upload).")
        return ("No interruptions: the time is the upload speed itself. If the average is close "
                "to what a speed test gives for upload, your internet connection is the limit; "
                "if it is far below, it is the Wi-Fi/local network or YouTube.")

    def _announce(self) -> None:
        """Status-bar news, and a signal for videos that belong to a match."""
        for row in self.rows:
            key = (row["id"], row["status"])
            if row["status"] in (UPLOADED, COMPLETE, FAILED) and key not in self._announced:
                self._announced.add(key)
                if not self._first_refresh:
                    self.message.emit({UPLOADED: f"{row['name']}: YouTube has the whole file",
                                       COMPLETE: f"{row['name']}: ready on YouTube — {row['url']}",
                                       FAILED: f"{row['name']}: {row['error']}"}[row["status"]])
            if row["video_id"] and row["link"] and not row["link_done"] \
                    and time.monotonic() - self._link_asked.get(row["id"], -60) > 5:
                self._link_asked[row["id"]] = time.monotonic()
                self.videoReady.emit(row)
        self._first_refresh = False

    _first_refresh = True  # what is already finished at start is not news

    def linked(self, job_id: str) -> None:
        """The main window has put this job's video id into its match."""
        if self.manager is not None:
            self.manager.mark_linked(job_id)

    # -- buttons -----------------------------------------------------------------------

    def _act(self, what: str) -> None:
        row = self._selected()
        if row is not None and self.manager is not None:
            getattr(self.manager, what)(row["id"])
            self.refresh()

    def _hold(self, holding: bool) -> None:
        if self.manager is not None:
            self.manager.hold(holding)
            self.hold_button.setText("Resume all" if holding else "Pause all")
            self.refresh()

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def restart_selected(self) -> None:
        row = self._selected()
        if row is None or self.manager is None:
            return
        if self.confirm("Restart from the beginning?",
                        f"{row['name']}: {size_text(row['confirmed'])} already uploaded will be "
                        "sent again from the start. Restart?"):
            try:
                self.manager.restart(row["id"])
            except OSError as exc:
                self.message.emit(f"Cannot restart {row['name']}: {exc}")
            self.refresh()

    def remove_selected(self) -> None:
        row = self._selected()
        if row is None or self.manager is None:
            return
        if not row["video_id"] and row["confirmed"] and not self.confirm(
                "Remove the upload?", f"{row['name']} is {row['percent']:.0f}% uploaded. Remove it "
                "from the queue? (The video file itself is not touched.)"):
            return
        if not self.manager.remove(row["id"]):
            self.message.emit("Pause the upload before removing it")
        self.refresh()

    def open_selected(self) -> None:
        row = self._selected()
        if row and row["url"]:
            self.open_browser(row["url"])

    def copy_link(self) -> None:
        row = self._selected()
        if row and row["url"]:
            QGuiApplication.clipboard().setText(row["url"])
            self.message.emit("Link copied")
