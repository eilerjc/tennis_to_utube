"""Step 1 — Files: pick a folder and the video files of a match (DESIGN.md §12).

A custom browser panel (Qt's standard dialog cannot do siblings): path bar, Up, sibling
folders, recent folders and pinned favourites. Video files are listed in the proposed
order (GoPro recording, then chapter) with their duration and size; ffprobe runs in the
background. The user ticks the files, adjusts the order, enters the players and the format,
and creates the match file — or opens a match file already in the folder.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import matchfile, sources
from ..appstate import AppState, save_state
from ..config import Config
from ..probe import MediaInfo, Tools, probe
from ..scoring import PRESET_LABELS


def _duration_text(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _size_text(n: int | None) -> str:
    if n is None:
        return ""
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


class _Prober(QObject):
    """Runs ffprobe on a list of files in a worker thread."""

    probed = Signal(str, object)  # path, MediaInfo or error message (str)

    def __init__(self, tools: Tools):
        super().__init__()
        self.tools = tools

    @Slot(list)
    def run(self, paths: list[str]) -> None:
        for p in paths:
            try:
                self.probed.emit(p, probe(p, self.tools))
            except Exception as exc:  # report every failure in the table
                self.probed.emit(p, str(exc))


class FilesPage(QWidget):
    matchReady = Signal(object, object)  # (MatchFile, Path to the match file)
    _probeRequest = Signal(list)

    COLUMNS = ("Use", "File", "Duration", "Size", "Created", "Check")

    def __init__(self, config: Config, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.folder: Path | None = None
        self.order: list[Path] = []
        self.ticked: set[Path] = set()
        self.infos: dict[Path, MediaInfo | str] = {}

        self._thread = QThread(self)
        self._prober = _Prober(Tools.from_config(config))
        self._prober.moveToThread(self._thread)
        self._probeRequest.connect(self._prober.run)
        self._prober.probed.connect(self._on_probed)
        self._thread.start()

        # Navigation row
        self.up_button = QPushButton("Up")
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Folder with the match videos")
        self.siblings = QComboBox()
        self.siblings.setMinimumWidth(220)
        self.recent = QComboBox()
        self.recent.setMinimumWidth(220)
        self.favorite_button = QPushButton("☆ Pin folder")
        self.favorites = QComboBox()
        self.favorites.setMinimumWidth(220)
        nav = QHBoxLayout()
        nav.addWidget(self.up_button)
        nav.addWidget(self.path_edit, 1)
        for label, combo in (("Siblings", self.siblings), ("Recent", self.recent),
                             ("Pinned", self.favorites)):
            nav.addWidget(QLabel(label))
            nav.addWidget(combo)
        nav.addWidget(self.favorite_button)

        # Existing match files
        self.matches = QListWidget()
        self.matches.setMaximumHeight(90)
        self.open_button = QPushButton("Open match")
        existing = QGroupBox("Matches in this folder")
        ex_layout = QHBoxLayout(existing)
        ex_layout.addWidget(self.matches, 1)
        ex_layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignTop)

        # Video files
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(self.COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.move_up = QPushButton("Move up")
        self.move_down = QPushButton("Move down")
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color: #b35c00")
        side_buttons = QVBoxLayout()
        side_buttons.addWidget(self.move_up)
        side_buttons.addWidget(self.move_down)
        side_buttons.addStretch(1)
        files_box = QGroupBox("Videos (ticked files are joined in this order)")
        files_layout = QVBoxLayout(files_box)
        row = QHBoxLayout()
        row.addWidget(self.table, 1)
        row.addLayout(side_buttons)
        files_layout.addLayout(row)
        files_layout.addWidget(self.notes)

        # Match setup
        self.singles = QRadioButton("Singles")
        self.doubles = QRadioButton("Doubles")
        self.singles.setChecked(True)
        kind = QHBoxLayout()
        kind.addWidget(self.singles)
        kind.addWidget(self.doubles)
        kind.addStretch(1)
        self.names = {side: [QLineEdit(), QLineEdit()] for side in "AB"}
        for side, edits in self.names.items():
            for i, edit in enumerate(edits):
                edit.setPlaceholderText(f"Player {'AB'.index(side) * 2 + i + 1}")
        self.format = QComboBox()
        for key, label in PRESET_LABELS.items():
            self.format.addItem(label, key)
        default = config.get("scoring.default_format")
        if default in PRESET_LABELS:
            self.format.setCurrentIndex(list(PRESET_LABELS).index(default))
        self.no_ad = QCheckBox("No-ad")
        self.coman = QCheckBox("Coman tiebreak")
        self.coman.setToolTip("Change ends after the 1st tiebreak point, then every 4 (instead of every 6)")
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(self.format, 1)
        fmt_row.addWidget(self.no_ad)
        fmt_row.addWidget(self.coman)
        setup = QGroupBox("New match")
        form = QFormLayout(setup)
        form.addRow("Match", kind)
        form.addRow("Side A (ours)", self._pair(self.names["A"]))
        form.addRow("Side B", self._pair(self.names["B"]))
        form.addRow("Format", fmt_row)
        self.create_button = QPushButton("Create match")
        form.addRow("", self.create_button)
        setup.setMaximumWidth(900)

        layout = QVBoxLayout(self)
        layout.addLayout(nav)
        layout.addWidget(existing)
        layout.addWidget(files_box, 1)
        layout.addWidget(setup)

        self.up_button.clicked.connect(lambda: self.folder and self.go_to(self.folder.parent))
        self.path_edit.returnPressed.connect(lambda: self.go_to(self.path_edit.text()))
        for combo in (self.siblings, self.recent, self.favorites):
            combo.activated.connect(lambda _i, c=combo: c.currentData() and self.go_to(c.currentData()))
        self.favorite_button.clicked.connect(self._toggle_favorite)
        self.open_button.clicked.connect(self._open_selected_match)
        self.matches.itemDoubleClicked.connect(lambda _item: self._open_selected_match())
        self.table.itemChanged.connect(self._on_item_changed)
        self.move_up.clicked.connect(lambda: self._move(-1))
        self.move_down.clicked.connect(lambda: self._move(1))
        self.doubles.toggled.connect(self._update_kind)
        self.create_button.clicked.connect(self.create_match)
        self._update_kind()
        self._refresh_combos()

    @staticmethod
    def _pair(edits: list[QLineEdit]) -> QHBoxLayout:
        row = QHBoxLayout()
        for e in edits:
            row.addWidget(e)
        return row

    def shutdown(self) -> None:
        self._thread.quit()
        self._thread.wait()

    # -- navigation -------------------------------------------------------------

    def go_to(self, folder: str | Path) -> None:
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            self.notes.setText(f"Not a folder: {folder}")
            return
        self.folder = folder.resolve()
        self.path_edit.setText(str(self.folder))
        self.state.visit(self.folder)
        self._save_state()
        self.order = sources.list_videos(self.folder)
        groups = sources.group_by_recording(self.order)
        first = next(iter(groups.values()), [])
        self.ticked = set(first)  # propose the first recording
        self.infos = {}
        self._fill_matches()
        self._refresh_combos()
        self._fill_table()
        notes = []
        if len(groups) > 1:
            notes.append(f"{len(groups)} recordings in this folder; the first is ticked. "
                         "Tick the files that belong to this match.")
        self.notes.setText(" ".join(notes))
        if self.order:
            self._probeRequest.emit([str(p) for p in self.order])

    def _refresh_combos(self) -> None:
        def fill(combo: QComboBox, folders: list, current: Path | None) -> None:
            combo.blockSignals(True)
            combo.clear()
            for f in folders:
                combo.addItem(Path(f).name or str(f), str(f))
                combo.setItemData(combo.count() - 1, str(f), Qt.ItemDataRole.ToolTipRole)
            if current is not None:
                i = combo.findData(str(current))
                combo.setCurrentIndex(i)
            combo.blockSignals(False)

        fill(self.siblings, sources.sibling_folders(self.folder) if self.folder else [], self.folder)
        fill(self.recent, self.state.recent, None)
        fill(self.favorites, self.state.favorites, None)
        pinned = self.folder is not None and str(self.folder) in self.state.favorites
        self.favorite_button.setText("★ Unpin folder" if pinned else "☆ Pin folder")

    def _toggle_favorite(self) -> None:
        if self.folder is not None:
            self.state.toggle_favorite(self.folder)
            self._save_state()
            self._refresh_combos()

    def _save_state(self) -> None:
        try:
            save_state(self.state)
        except OSError:
            pass  # remembering folders is a convenience only

    # -- existing matches --------------------------------------------------------

    def _fill_matches(self) -> None:
        self.matches.clear()
        for p in sources.list_matches(self.folder):
            self.matches.addItem(p.name)
        self.open_button.setEnabled(self.matches.count() > 0)

    def _open_selected_match(self) -> None:
        item = self.matches.currentItem() or self.matches.item(0)
        if item is None or self.folder is None:
            return
        self.open_match(self.folder / item.text())

    def open_match(self, path: Path) -> None:
        try:
            mf = matchfile.load(path)
        except (OSError, matchfile.MatchFileError) as exc:
            QMessageBox.warning(self, "Cannot open match", f"{path.name}: {exc}")
            return
        missing = [s.path for s in mf.sources
                   if not matchfile.resolve_source_path(s.path, path).exists()]
        if missing:
            QMessageBox.warning(self, "Missing videos",
                                "These videos were not found:\n" + "\n".join(missing))
        self.matchReady.emit(mf, path)

    # -- file table --------------------------------------------------------------

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.order))
        for row, path in enumerate(self.order):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked if path in self.ticked else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, use)
            info = self.infos.get(path)
            if isinstance(info, MediaInfo):
                cells = [path.name, _duration_text(info.duration_ms), _size_text(info.size_bytes),
                         info.creation_time.strftime("%Y-%m-%d %H:%M") if info.creation_time else "",
                         ""]
            else:
                cells = [path.name, "", "", "", info or "reading…"]
            for col, text in enumerate(cells, start=1):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self._check_selection()
        self.table.blockSignals(False)

    def _selected_infos(self) -> list[MediaInfo]:
        return [self.infos[p] for p in self.order
                if p in self.ticked and isinstance(self.infos.get(p), MediaInfo)]

    def _check_selection(self) -> None:
        """Mark files that cannot be joined with the first ticked file, and order warnings."""
        infos = self._selected_infos()
        problems = {i.message.split()[0]: i.message for i in sources.check_join_compatible(infos)}
        for row, path in enumerate(self.order):
            info = self.infos.get(path)
            if isinstance(info, MediaInfo):
                text = problems.get(path.name, "OK" if path in self.ticked else "")
                self.table.item(row, 5).setText(text)
        order_issues = sources.check_creation_order(infos)
        self.create_button.setToolTip("\n".join(i.message for i in order_issues))

    @Slot(str, object)
    def _on_probed(self, path: str, info: object) -> None:
        p = Path(path)
        if p in self.order:
            self.infos[p] = info  # type: ignore[assignment]
            self._fill_table()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        path = self.order[item.row()]
        if item.checkState() == Qt.CheckState.Checked:
            self.ticked.add(path)
        else:
            self.ticked.discard(path)
        self.table.blockSignals(True)
        self._check_selection()
        self.table.blockSignals(False)

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < len(self.order):
            return
        self.order[row], self.order[new] = self.order[new], self.order[row]
        self._fill_table()
        self.table.selectRow(new)

    # -- creating a match ---------------------------------------------------------------

    def _update_kind(self) -> None:
        doubles = self.doubles.isChecked()
        for side in "AB":
            self.names[side][1].setVisible(doubles)
            base = "AB".index(side) * (2 if doubles else 1)
            for i, edit in enumerate(self.names[side]):
                edit.setPlaceholderText(f"Player {base + i + 1}")

    def create_match(self) -> Path | None:
        if self.folder is None:
            return None
        chosen = [p for p in self.order if p in self.ticked]
        if not chosen:
            QMessageBox.information(self, "No videos", "Tick the videos of this match first.")
            return None
        not_ready = [p.name for p in chosen if not isinstance(self.infos.get(p), MediaInfo)]
        if not_ready:
            QMessageBox.information(self, "Not ready",
                                    "These files could not be read (yet):\n" + "\n".join(not_ready))
            return None
        infos = [self.infos[p] for p in chosen]
        errors = sources.check_join_compatible(infos)
        if errors:
            QMessageBox.warning(self, "Cannot join losslessly",
                                "\n".join(i.message for i in errors))
            return None
        path = matchfile.default_match_path(chosen[0])
        if path.exists():
            answer = QMessageBox.question(
                self, "Match exists", f"{path.name} already exists. Open it instead?")
            if answer == QMessageBox.StandardButton.Yes:
                self.open_match(path)
            return None
        doubles = self.doubles.isChecked()
        count = 2 if doubles else 1
        fmt = {"preset": self.format.currentData()}
        if self.no_ad.isChecked():
            fmt["ad"] = False
        if self.coman.isChecked():
            fmt["tiebreak_changeovers"] = "coman"
        mf = sources.new_match_file(
            infos, path, kind="doubles" if doubles else "singles",
            side_a=[e.text() for e in self.names["A"][:count]],
            side_b=[e.text() for e in self.names["B"][:count]], format_spec=fmt)
        try:
            matchfile.save(mf, path)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot save", f"{path}: {exc}")
            return None
        self._fill_matches()
        self.matchReady.emit(mf, path)
        return path
