"""Main window: the four steps of the workflow (Files → Mark → Trim → Export), or Files →
Cut for Cut only (DESIGN.md §12b)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget

from ..appstate import AppState, save_state
from ..config import Config
from ..matchfile import MatchFile
from ..shortcuts import Shortcuts, default_shortcuts
from .cut_page import CutPage
from .export_page import ExportPage
from .files_page import FilesPage
from .mark_page import MarkPage
from .player import create_player
from .trim_page import TrimPage
from .upload_page import UploadPage

APP_TITLE = "Tennis to YouTube"
DEFAULT_SIZE = (1920, 1200)  # first start, shrunk to fit the screen
GEOMETRY_KEY = "window"  # state.json: [x, y, width, height] of the normal (not maximized) window


class MainWindow(QMainWindow):
    def __init__(self, config: Config, state: AppState, shortcuts: Shortcuts | None = None,
                 player_factory=create_player, parent: QWidget | None = None,
                 upload_config: Config | None = None):
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
        self.cut = CutPage(config, shortcuts, player_factory)
        self.upload = UploadPage(state, upload_config)
        self.steps = QTabWidget()
        self.steps.setDocumentMode(True)
        for label, page in (("1  Files", self.files), ("2  Mark", self.mark),
                            ("3  Trim", self.trim), ("4  Export", self.export),
                            ("2  Cut", self.cut), ("Upload", self.upload)):
            self.steps.addTab(page, label)
        self.setCentralWidget(self.steps)
        self._set_match_steps_enabled(False)
        self.steps.setTabVisible(self.CUT_TAB, False)

        self.files.matchReady.connect(self.open_match)
        self.files.cutReady.connect(self.open_cut)
        self.cut.message.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.mark.message.connect(lambda text: self.statusBar().showMessage(text, 5000))
        self.trim.message.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.trim.changed.connect(self.mark.external_change)
        self.trim.showTime.connect(self._show_time)
        self.export.message.connect(lambda text: self.statusBar().showMessage(text, 5000))
        self.export.changed.connect(self.mark.external_change)
        self.upload.message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.upload.videoReady.connect(self._video_uploaded)
        self.files.uploadRequested.connect(self.upload_videos)
        self.cut.uploadRequested.connect(lambda path: self.upload_videos([path]))
        self.trim.uploadRequested.connect(self.upload_trimmed)
        self.export.uploadRequested.connect(self.upload_recording)
        self.steps.currentChanged.connect(self._step_changed)
        warnings = config.warnings + shortcuts.warnings
        if warnings:
            self.statusBar().showMessage("  |  ".join(warnings), 30000)

    MATCH_TABS = (1, 2, 3)
    CUT_TAB = 4
    UPLOAD_TAB = 5  # always there: any video can be uploaded

    # -- uploading (DESIGN.md §12c) ------------------------------------------------------

    def upload_videos(self, paths, texts=None, link=None) -> list[str]:
        """Go to the Upload step and queue these videos (after asking for their details)."""
        self.steps.setCurrentWidget(self.upload)
        return self.upload.add_videos([Path(p) for p in paths], texts=texts, link=link)

    def _match_texts(self, video: Path, chapters: str, trimmed: bool) -> list[dict[str, str]]:
        from .. import uploadpresets
        from ..youtube import match_summary

        session = self.mark.session
        summary = match_summary(session.mf, session.analysis)
        sources = session.source_paths()
        date = uploadpresets.file_date(sources[0] if sources else video)
        return [uploadpresets.match_details(summary, date, chapters, trimmed)]

    def upload_trimmed(self) -> list[str]:
        """The trimmed video of the open match: named after the players, with its chapters;
        its YouTube link goes into the match when the upload is through."""
        session = self.mark.session
        video = session.output_path() if session is not None else None
        if session is None or video is None or not Path(video).is_file():
            self.statusBar().showMessage("Make the trimmed video first", 8000)
            return []
        session.save()
        texts = self._match_texts(Path(video), self.trim.chapters.toPlainText(), trimmed=True)
        return self.upload_videos([video], texts,
                                  {"match": str(self.match_path), "target": "trimmed"})

    def upload_recording(self) -> list[str]:
        """The full recording of the open match, when it is a single file."""
        session = self.mark.session
        sources = session.source_paths() if session is not None else []
        if session is None or len(sources) != 1:
            self.statusBar().showMessage(
                "The recording is several files: join them with Cut only, then upload that video",
                10000)
            return []
        session.save()
        chapters = self.export.export.description if self.export.export else ""
        texts = self._match_texts(sources[0], chapters, trimmed=False)
        return self.upload_videos(sources, texts, {"match": str(self.match_path), "target": "full"})

    def _video_uploaded(self, row: dict) -> None:
        """An uploaded video belongs to a match: put its YouTube link there (the open
        match through its pages, so they show it; any other straight into its file)."""
        from ..uploadtool import apply_link

        link, video_id = row["link"], row["video_id"]
        session = self.mark.session
        if session is not None and self.match_path is not None \
                and Path(link.get("match", "")) == self.match_path:
            if link.get("target") == "trimmed":
                made = session.output_path()
                try:
                    same = made is not None and Path(made).resolve() == Path(row["path"]) \
                        and Path(made).stat().st_mtime_ns == row["mtime_ns"]
                except OSError:
                    same = False
                if not same:
                    self.upload.linked(row["id"])  # another video was made since
                    return
                if self.trim.busy():
                    return  # asked again in a few seconds
                self.trim.video.setText(video_id)
                self.trim._video_entered()
            else:
                self.export.video.setText(video_id)
                self.export._video_entered()
            self.upload.linked(row["id"])
            self.statusBar().showMessage(f"{row['name']} is on YouTube: its link is in the match", 10000)
            return
        try:
            said = apply_link(self.upload.manager.jobs[row["id"]])
        except Exception as exc:  # the upload itself is fine; say so and move on
            said = f"Could not put the link into {Path(link.get('match', '')).name}: {exc}"
        self.upload.linked(row["id"])
        if said:
            self.statusBar().showMessage(said, 10000)

    def _set_match_steps_enabled(self, enabled: bool) -> None:
        for i in self.MATCH_TABS:
            self.steps.setTabEnabled(i, enabled)

    def _show_mode(self, cut: bool) -> None:
        """Cut only shows Files → Cut; a match shows Files → Mark → Trim → Export."""
        for i in self.MATCH_TABS:
            self.steps.setTabVisible(i, not cut)
        self.steps.setTabVisible(self.CUT_TAB, cut)

    def open_cut(self, infos) -> None:
        """Cut only: the videos (MediaInfo, in order) go to the Cut step."""
        if self._cut_is_making() or not self.cut.confirm_discard():
            return
        self.mark.save()
        self.mark.player.set_paused(True)
        self.match_path = None
        self.setWindowTitle(f"{APP_TITLE} — Cut only")
        self._show_mode(cut=True)
        self.cut.load(infos)
        self.steps.setCurrentWidget(self.cut)
        self.statusBar().showMessage(
            f"Cut only: {len(infos)} video{'s' * (len(infos) != 1)} — mark the parts to remove", 8000)

    @property
    def match(self) -> MatchFile | None:
        """The open match (owned by the Mark step's session)."""
        return self.mark.match

    def _cut_is_making(self) -> bool:
        """True (after showing the Cut step and saying so) while Cut only is making a video:
        loading other videos now would mix the two up."""
        if not self.cut.is_making():
            return False
        self.steps.setCurrentWidget(self.cut)
        self.cut.tell_making()
        return True

    def open_match(self, mf: MatchFile, path: Path) -> None:
        if self._cut_is_making() or not self.cut.confirm_discard():
            return
        self.cut.player.set_paused(True)
        self._show_mode(cut=False)
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
        if page is self.cut:
            self.cut.setFocus()
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
        if not self.cut.confirm_stop_making() or not self.cut.confirm_discard() \
                or not self.upload.confirm_close():
            event.ignore()
            return
        self.remember_geometry()
        self.files.shutdown()
        self.trim.shutdown()
        self.mark.shutdown()
        self.cut.shutdown()
        self.upload.shutdown()
        super().closeEvent(event)
