"""Step 1 — Files: pick a folder and the video files of a match (DESIGN.md §12).

An Explorer-like browser: Back/Forward/Up/Refresh, an address bar (with completion), Quick
access (pinned and recent folders) and a folder tree on the left, and the current folder's
subfolders and match files (with what each folder holds, read in the background) on the
right. Video files are listed in the proposed
order (GoPro recording, then chapter) with their duration and size; ffprobe runs in the
background. The user ticks the files, adjusts the order, enters the players and the format,
and creates the match file — or opens a match file already in the folder.
"""

from __future__ import annotations

from pathlib import Path

from datetime import datetime

from PySide6.QtCore import QDir, QEvent, QModelIndex, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileIconProvider, QFileSystemModel,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QToolButton, QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import matchfile, sources
from ..appstate import AppState, History, save_state
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


def _modified_text(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


class _Prober(QObject):
    """Runs ffprobe on a list of files in a worker thread. A newer request (higher
    ``generation``, set from the GUI thread) makes an older one stop early."""

    probed = Signal(str, object)  # path, MediaInfo or error message (str)

    def __init__(self, tools: Tools):
        super().__init__()
        self.tools = tools
        self.generation = 0

    @Slot(int, list)
    def run(self, generation: int, paths: list[str]) -> None:
        for p in paths:
            if generation != self.generation:
                return
            try:
                self.probed.emit(p, probe(p, self.tools))
            except Exception as exc:  # report every failure in the table
                self.probed.emit(p, str(exc))


class _Scanner(QObject):
    """Reads what each subfolder holds (slow on network drives) in a worker thread."""

    scanned = Signal(str, object)  # path, FolderSummary or None

    def __init__(self):
        super().__init__()
        self.generation = 0

    @Slot(int, list)
    def run(self, generation: int, paths: list[str]) -> None:
        for p in paths:
            if generation != self.generation:
                return
            self.scanned.emit(p, sources.folder_summary(p))


class FilesPage(QWidget):
    matchReady = Signal(object, object)  # (MatchFile, Path to the match file)
    _probeRequest = Signal(int, list)
    _scanRequest = Signal(int, list)

    COLUMNS = ("Use", "File", "Duration", "Size", "Created", "Check")
    KEYBOARD_NAV_DELAY_MS = 350  # arrowing through the tree opens a folder after a pause

    def __init__(self, config: Config, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.folder: Path | None = None
        self.history = History()
        self.order: list[Path] = []
        self.ticked: set[Path] = set()
        self.infos: dict[Path, MediaInfo | str] = {}
        self._syncing_tree = False
        self._folder_items: dict[str, QTreeWidgetItem] = {}

        self._thread = QThread(self)
        self._prober = _Prober(Tools.from_config(config))
        self._prober.moveToThread(self._thread)
        self._probeRequest.connect(self._prober.run)
        self._prober.probed.connect(self._on_probed)
        self._thread.start()
        self._scan_thread = QThread(self)
        self._scanner = _Scanner()
        self._scanner.moveToThread(self._scan_thread)
        self._scanRequest.connect(self._scanner.run)
        self._scanner.scanned.connect(self._on_scanned)
        self._scan_thread.start()

        style = self.style()
        icons = QFileIconProvider()
        self._folder_icon = icons.icon(QFileIconProvider.IconType.Folder)
        self._file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        # Toolbar: Back, Forward, Up, Refresh, address bar, Pin
        def tool(pixmap: QStyle.StandardPixmap, tip: str) -> QToolButton:
            b = QToolButton()
            b.setIcon(style.standardIcon(pixmap))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            return b

        self.back_button = tool(QStyle.StandardPixmap.SP_ArrowBack, "Back (Alt+Left)")
        self.forward_button = tool(QStyle.StandardPixmap.SP_ArrowForward, "Forward (Alt+Right)")
        self.up_button = tool(QStyle.StandardPixmap.SP_ArrowUp, "Up to the parent folder (Alt+Up)")
        self.refresh_button = tool(QStyle.StandardPixmap.SP_BrowserReload, "Refresh (F5)")
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Folder with the match videos — type or paste a path")
        self.favorite_button = QPushButton("☆ Pin folder")
        nav = QHBoxLayout()
        for w in (self.back_button, self.forward_button, self.up_button, self.refresh_button):
            nav.addWidget(w)
        nav.addWidget(self.path_edit, 1)
        nav.addWidget(self.favorite_button)

        # Left: Quick access (pinned, recent) and the folder tree
        self.quick = QListWidget()
        quick_box = QGroupBox("Quick access")
        QVBoxLayout(quick_box).addWidget(self.quick)
        self.fs_model = QFileSystemModel(self)
        self.fs_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        self.fs_model.setRootPath("")
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        for col in range(1, self.fs_model.columnCount()):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        tree_box = QGroupBox("Folders")
        QVBoxLayout(tree_box).addWidget(self.tree)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(quick_box)
        left.addWidget(tree_box)
        left.setStretchFactor(1, 1)
        left.setSizes([220, 600])
        completer = QCompleter(self.fs_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.path_edit.setCompleter(completer)

        # Right, top: what the current folder holds (folders, then match files)
        self.contents = QTreeWidget()
        self.contents.setColumnCount(3)
        self.contents.setHeaderLabels(["Name", "Holds", "Modified"])
        self.contents.setRootIsDecorated(False)
        self.contents.setUniformRowHeights(True)
        self.contents.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.contents.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.open_button = QPushButton("Open")
        self.open_button.setToolTip("Open the selected folder or match (or double-click it)")
        self.contents_box = QGroupBox("In this folder — double-click a folder to go in, a match to open it")
        ex_layout = QHBoxLayout(self.contents_box)
        ex_layout.addWidget(self.contents, 1)
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

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.contents_box, 2)
        right_layout.addWidget(files_box, 3)
        right_layout.addWidget(setup)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1500])
        layout = QVBoxLayout(self)
        layout.addLayout(nav)
        layout.addWidget(split, 1)

        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)
        self.up_button.clicked.connect(self.go_up)
        self.refresh_button.clicked.connect(self.refresh)
        self.path_edit.returnPressed.connect(lambda: self.go_to(self.path_edit.text()))
        self.favorite_button.clicked.connect(self._toggle_favorite)
        self.quick.itemClicked.connect(self._on_quick_clicked)
        self.tree.clicked.connect(self._on_tree_clicked)
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        self._tree_timer = QTimer(self)
        self._tree_timer.setSingleShot(True)
        self._tree_timer.setInterval(self.KEYBOARD_NAV_DELAY_MS)
        self._tree_timer.timeout.connect(self._open_tree_current)
        self.contents.itemActivated.connect(lambda item, _col: self._open_item(item))
        self.open_button.clicked.connect(self._open_selected)
        for keys, slot in (("Alt+Left", self.go_back), ("Alt+Right", self.go_forward),
                           ("Alt+Up", self.go_up), ("F5", self.refresh),
                           ("Ctrl+L", self._focus_address)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        up = QShortcut(QKeySequence("Backspace"), self.contents)
        up.setContext(Qt.ShortcutContext.WidgetShortcut)
        up.activated.connect(self.go_up)
        for view in (self.contents, self.tree, self.quick):
            view.viewport().installEventFilter(self)  # mouse back/forward buttons
        self.table.itemChanged.connect(self._on_item_changed)
        self.move_up.clicked.connect(lambda: self._move(-1))
        self.move_down.clicked.connect(lambda: self._move(1))
        self.doubles.toggled.connect(self._update_kind)
        self.create_button.clicked.connect(self.create_match)
        self._update_kind()
        self._refresh_quick()

    @staticmethod
    def _pair(edits: list[QLineEdit]) -> QHBoxLayout:
        row = QHBoxLayout()
        for e in edits:
            row.addWidget(e)
        return row

    def shutdown(self) -> None:
        for thread, worker in ((self._thread, self._prober), (self._scan_thread, self._scanner)):
            worker.generation += 1  # stop queued work early
            thread.quit()
            thread.wait()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.BackButton:
                self.go_back()
                return True
            if event.button() == Qt.MouseButton.ForwardButton:
                self.go_forward()
                return True
        return super().eventFilter(obj, event)

    # -- navigation -------------------------------------------------------------

    def go_to(self, folder: str | Path, record: bool = True) -> bool:
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            self.notes.setText(f"Not a folder: {folder}")
            return False
        self.folder = folder.resolve()
        if record:
            self.history.visit(self.folder)
        self.path_edit.setText(str(self.folder))
        self.state.visit(self.folder)
        self._save_state()
        self._load_folder()
        return True

    def go_back(self) -> None:
        target = self.history.go_back()
        if target is not None:
            self.go_to(target, record=False)

    def go_forward(self) -> None:
        target = self.history.go_forward()
        if target is not None:
            self.go_to(target, record=False)

    def go_up(self) -> None:
        if self.folder is not None and self.folder.parent != self.folder:
            child = self.folder
            if self.go_to(self.folder.parent):
                self._select_in_contents(child)  # like Explorer: the folder you came from

    def refresh(self) -> None:
        if self.folder is not None:
            self._load_folder()

    def _focus_address(self) -> None:
        self.path_edit.setFocus()
        self.path_edit.selectAll()

    def _load_folder(self) -> None:
        self.order = sources.list_videos(self.folder)
        groups = sources.group_by_recording(self.order)
        first = next(iter(groups.values()), [])
        self.ticked = set(first)  # propose the first recording
        self.infos = {}
        self._fill_contents()
        self._refresh_quick()
        self._update_buttons()
        self._sync_tree()
        self._fill_table()
        notes = []
        if len(groups) > 1:
            notes.append(f"{len(groups)} recordings in this folder; the first is ticked. "
                         "Tick the files that belong to this match.")
        self.notes.setText(" ".join(notes))
        self._prober.generation += 1
        if self.order:
            self._probeRequest.emit(self._prober.generation, [str(p) for p in self.order])

    def _update_buttons(self) -> None:
        self.back_button.setEnabled(bool(self.history.back))
        self.forward_button.setEnabled(bool(self.history.forward))
        self.up_button.setEnabled(self.folder is not None and self.folder.parent != self.folder)
        pinned = self.folder is not None and str(self.folder) in self.state.favorites
        self.favorite_button.setText("★ Unpin folder" if pinned else "☆ Pin folder")

    # Quick access ---------------------------------------------------------------

    def _refresh_quick(self) -> None:
        self.quick.clear()

        def heading(text: str) -> None:
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            font = QFont()
            font.setBold(True)
            item.setFont(font)
            self.quick.addItem(item)

        for title, folders, mark in (("Pinned", self.state.favorites, "★ "),
                                     ("Recent", self.state.recent, "")):
            if not folders:
                continue
            heading(title)
            names = [Path(f).name or f for f in folders]
            for f, name in zip(folders, names):
                if names.count(name) > 1:  # same name in different places: add the parent
                    name += f"  ({Path(f).parent.name or Path(f).parent})"
                item = QListWidgetItem(self._folder_icon, mark + name)
                item.setData(Qt.ItemDataRole.UserRole, f)
                item.setToolTip(f)
                self.quick.addItem(item)
        self._update_buttons()

    def _on_quick_clicked(self, item: QListWidgetItem) -> None:
        folder = item.data(Qt.ItemDataRole.UserRole)
        if folder:
            self.go_to(folder)

    def _toggle_favorite(self) -> None:
        if self.folder is not None:
            self.state.toggle_favorite(self.folder)
            self._save_state()
            self._refresh_quick()

    def _save_state(self) -> None:
        try:
            save_state(self.state)
        except OSError:
            pass  # remembering folders is a convenience only

    # Folder tree -------------------------------------------------------------------

    def _sync_tree(self) -> None:
        """Show and select the current folder in the tree (expanding its parents)."""
        index = self.fs_model.index(str(self.folder))
        if not index.isValid():
            return
        self._syncing_tree = True
        try:
            parent = index.parent()
            while parent.isValid():
                self.tree.expand(parent)
                parent = parent.parent()
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
        finally:
            self._syncing_tree = False

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        self._tree_timer.stop()
        path = Path(self.fs_model.filePath(index))
        if path != self.folder:
            self.go_to(path)

    def _on_tree_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not self._syncing_tree and current.isValid():
            self._tree_timer.start()  # keyboard: open after a short pause

    def _open_tree_current(self) -> None:
        index = self.tree.currentIndex()
        if index.isValid():
            path = Path(self.fs_model.filePath(index))
            if path != self.folder:
                self.go_to(path)

    # -- folder contents: subfolders and match files ------------------------------------

    def _fill_contents(self) -> None:
        self.contents.clear()
        self._folder_items = {}
        folders = sources.list_subfolders(self.folder)
        for f in folders:
            item = QTreeWidgetItem([f.name, "…", _modified_text(f)])
            item.setIcon(0, self._folder_icon)
            item.setData(0, Qt.ItemDataRole.UserRole, ("folder", str(f)))
            self.contents.addTopLevelItem(item)
            self._folder_items[str(f)] = item
        for m in sources.list_matches(self.folder):
            item = QTreeWidgetItem([m.name, "Match", _modified_text(m)])
            item.setIcon(0, self._file_icon)
            item.setData(0, Qt.ItemDataRole.UserRole, ("match", str(m)))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self.contents.addTopLevelItem(item)
        self.open_button.setEnabled(self.contents.topLevelItemCount() > 0)
        self._scanner.generation += 1
        if folders:
            self._scanRequest.emit(self._scanner.generation, [str(f) for f in folders])

    @Slot(str, object)
    def _on_scanned(self, path: str, summary: object) -> None:
        item = self._folder_items.get(path)
        if item is None:
            return
        item.setText(1, (summary.text() or "empty") if summary is not None else "cannot read")
        if summary is not None and summary.matches:
            font = item.font(0)
            font.setBold(True)  # folders with matches stand out
            item.setFont(0, font)

    @property
    def matches(self) -> list[Path]:
        """Match files listed for the current folder."""
        out = []
        for i in range(self.contents.topLevelItemCount()):
            kind, path = self.contents.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            if kind == "match":
                out.append(Path(path))
        return out

    def _select_in_contents(self, path: Path) -> None:
        for i in range(self.contents.topLevelItemCount()):
            item = self.contents.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole)[1] == str(path):
                self.contents.setCurrentItem(item)
                return

    def _open_item(self, item: QTreeWidgetItem) -> None:
        kind, path = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "folder":
            self.go_to(path)
        else:
            self.open_match(Path(path))

    def _open_selected(self) -> None:
        item = self.contents.currentItem()
        if item is None:
            matches = self.matches
            if len(matches) == 1:
                self.open_match(matches[0])
            return
        self._open_item(item)

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
        self._fill_contents()
        self.matchReady.emit(mf, path)
        return path
