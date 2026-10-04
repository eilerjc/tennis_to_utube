"""Main window: the four steps of the workflow (Files → Mark → Trim → Export)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QWidget

from ..appstate import AppState
from ..config import Config
from ..matchfile import MatchFile
from ..shortcuts import Shortcuts, default_shortcuts
from .files_page import FilesPage
from .mark_page import MarkPage
from .player import create_player

APP_TITLE = "Tennis to YouTube"


class Placeholder(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.setWordWrap(True)
        self.setMargin(24)


class MainWindow(QMainWindow):
    def __init__(self, config: Config, state: AppState, shortcuts: Shortcuts | None = None,
                 player_factory=create_player, parent: QWidget | None = None):
        super().__init__(parent)
        shortcuts = shortcuts or default_shortcuts()
        self.config = config
        self.state = state
        self.match_path: Path | None = None
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1280, 800)

        self.files = FilesPage(config, state)
        self.mark = MarkPage(config, shortcuts, player_factory)
        self.trim: QWidget = Placeholder("Trimming comes after marking.")
        self.export: QWidget = Placeholder("Exporting comes after trimming.")
        self.steps = QTabWidget()
        self.steps.setDocumentMode(True)
        for label, page in (("1  Files", self.files), ("2  Mark", self.mark),
                            ("3  Trim", self.trim), ("4  Export", self.export)):
            self.steps.addTab(page, label)
        self.setCentralWidget(self.steps)
        self._set_match_steps_enabled(False)

        self.files.matchReady.connect(self.open_match)
        self.mark.message.connect(lambda text: self.statusBar().showMessage(text, 5000))
        warnings = config.warnings + shortcuts.warnings
        if warnings:
            self.statusBar().showMessage("  |  ".join(warnings), 30000)

    def _set_match_steps_enabled(self, enabled: bool) -> None:
        for i in (1, 2, 3):
            self.steps.setTabEnabled(i, enabled)

    @property
    def match(self) -> MatchFile | None:
        """The open match (owned by the Mark step's session)."""
        return self.mark.match

    def open_match(self, mf: MatchFile, path: Path) -> None:
        self.match_path = Path(path)
        self.setWindowTitle(f"{APP_TITLE} — {self.match_path.name}")
        self._set_match_steps_enabled(True)
        self.statusBar().showMessage(f"Opened {self.match_path}", 5000)
        self.mark.load_match(mf, self.match_path)
        self.steps.setCurrentIndex(1)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.files.shutdown()
        self.mark.shutdown()
        super().closeEvent(event)
