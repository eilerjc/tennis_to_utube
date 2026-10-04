"""Main window: the four steps of the workflow (Files → Mark → Trim → Export)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget

from ..appstate import AppState, save_state
from ..config import Config
from ..matchfile import MatchFile
from ..shortcuts import Shortcuts, default_shortcuts
from .export_page import ExportPage
from .files_page import FilesPage
from .mark_page import MarkPage
from .player import create_player
from .trim_page import TrimPage

APP_TITLE = "Tennis to YouTube"
DEFAULT_SIZE = (1920, 1200)  # first start, shrunk to fit the screen
GEOMETRY_KEY = "window"  # state.json: [x, y, width, height] of the normal (not maximized) window


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
        self.trim = TrimPage(config)
        self.export = ExportPage(config)
        self.steps = QTabWidget()
        self.steps.setDocumentMode(True)
        for label, page in (("1  Files", self.files), ("2  Mark", self.mark),
                            ("3  Trim", self.trim), ("4  Export", self.export)):
            self.steps.addTab(page, label)
        self.setCentralWidget(self.steps)
        self._set_match_steps_enabled(False)

        self.files.matchReady.connect(self.open_match)
        self.mark.message.connect(lambda text: self.statusBar().showMessage(text, 5000))
        self.trim.message.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.trim.changed.connect(self.mark.external_change)
        self.trim.showTime.connect(self._show_time)
        self.export.message.connect(lambda text: self.statusBar().showMessage(text, 5000))
        self.export.changed.connect(self.mark.external_change)
        self.steps.currentChanged.connect(self._step_changed)
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
        self.trim.load(self.mark.session)
        self.export.load(self.mark.session)
        self.steps.setCurrentIndex(1)

    def _step_changed(self, index: int) -> None:
        page = self.steps.widget(index)
        if page is self.trim and self.mark.session is not None:
            self.trim.refresh()
        if page is self.export and self.mark.session is not None:
            self.export.refresh()
        if page is self.mark:
            self.mark.setFocus()  # so the keys work straight away
        self.mark.save()

    def _show_time(self, t_ms: int) -> None:
        self.steps.setCurrentWidget(self.mark)
        self.mark.show_time(t_ms)

    def place_window(self) -> None:
        """Size and place the window (never maximized): where it was last time if that is
        still on a screen, else DEFAULT_SIZE (at most 90% of the screen), centred."""
        saved = self.state.extra.get(GEOMETRY_KEY)
        if (isinstance(saved, list) and len(saved) == 4
                and all(isinstance(v, int) and not isinstance(v, bool) for v in saved)):
            rect = QRect(*saved)
            if rect.width() > 0 and any(s.availableGeometry().intersects(rect)
                                        for s in QGuiApplication.screens()):
                self.setGeometry(rect)
                return
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        w = max(self.minimumWidth(), min(DEFAULT_SIZE[0], int(screen.width() * 0.9)))
        h = max(self.minimumHeight(), min(DEFAULT_SIZE[1], int(screen.height() * 0.9)))
        self.resize(w, h)
        self.move(screen.center().x() - w // 2, screen.center().y() - h // 2)

    def remember_geometry(self) -> None:
        g = self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        self.state.extra[GEOMETRY_KEY] = [g.x(), g.y(), g.width(), g.height()]
        try:
            save_state(self.state)
        except OSError:
            pass  # a convenience only

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.remember_geometry()
        self.files.shutdown()
        self.trim.shutdown()
        self.mark.shutdown()
        super().closeEvent(event)
