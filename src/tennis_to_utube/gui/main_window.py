"""Main window: the four steps of the workflow (Files → Mark → Trim → Export)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QWidget

from ..appstate import AppState
from ..config import Config
from ..matchfile import MatchFile
from .files_page import FilesPage

APP_TITLE = "Tennis to YouTube"


class Placeholder(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.setWordWrap(True)
        self.setMargin(24)


class MainWindow(QMainWindow):
    def __init__(self, config: Config, state: AppState, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.match: MatchFile | None = None
        self.match_path: Path | None = None
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1280, 800)

        self.files = FilesPage(config, state)
        self.mark: QWidget = Placeholder("Open or create a match on the Files step.")
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
        for w in config.warnings:
            self.statusBar().showMessage(w, 15000)

    def _set_match_steps_enabled(self, enabled: bool) -> None:
        for i in (1, 2, 3):
            self.steps.setTabEnabled(i, enabled)

    def open_match(self, mf: MatchFile, path: Path) -> None:
        self.match, self.match_path = mf, Path(path)
        self.setWindowTitle(f"{APP_TITLE} — {self.match_path.name}")
        self._set_match_steps_enabled(True)
        self.statusBar().showMessage(f"Opened {self.match_path}", 5000)
        self.steps.setCurrentIndex(1)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.files.shutdown()
        super().closeEvent(event)
