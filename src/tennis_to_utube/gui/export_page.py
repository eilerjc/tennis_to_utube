"""Step 4 — Export: YouTube description chapters and per-event links (DESIGN.md §8).

Times come from the video made on the Trim step (its stored plan), else from the original
recording. The video id is pasted after uploading; links then regenerate.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ..config import Config, effective_settings
from ..session import Session
from ..youtube import Export, build_export, links_csv, links_markdown, parse_video_id


def links_paths(match_path: Path) -> tuple[Path, Path]:
    stem = match_path.name.removesuffix(".match.json")
    return (match_path.with_name(f"{stem} links.md"), match_path.with_name(f"{stem} links.csv"))


class ExportPage(QWidget):
    changed = Signal()
    message = Signal(str)

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.session: Session | None = None
        self.export: Export | None = None

        self.video = QLineEdit()
        self.video.setPlaceholderText("Paste the YouTube link or video id after uploading")
        self.video.editingFinished.connect(self._video_entered)
        self.video_note = QLabel()
        id_row = QHBoxLayout()
        id_row.addWidget(QLabel("YouTube video:"))
        id_row.addWidget(self.video, 1)
        id_row.addWidget(self.video_note)
        self.note = QLabel()
        self.note.setWordWrap(True)

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.chapters = QPlainTextEdit()
        self.chapters.setReadOnly(True)
        self.chapters.setFont(mono)
        self.copy_button = QPushButton("Copy chapters")
        self.copy_button.clicked.connect(lambda *_: self.copy_chapters())
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Description chapters (paste into the YouTube description):"))
        left_layout.addWidget(self.chapters, 1)
        left_layout.addWidget(self.copy_button)

        self.links = QPlainTextEdit()
        self.links.setReadOnly(True)
        self.links.setFont(mono)
        self.save_button = QPushButton("Save links (Markdown + CSV)")
        self.save_button.clicked.connect(lambda *_: self.save_links())
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Links to every event, by chapter:"))
        right_layout.addWidget(self.links, 1)
        right_layout.addWidget(self.save_button)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        self.issues = QLabel()
        self.issues.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(id_row)
        layout.addWidget(self.note)
        layout.addWidget(split, 1)
        layout.addWidget(self.issues)

    def load(self, session: Session) -> None:
        self.session = session
        self.refresh()

    def refresh(self) -> None:
        if self.session is None:
            return
        mf = self.session.mf
        self.video.setText(mf.video_id or "")
        plan, note = self.session.export_plan()
        settings = effective_settings(self.config, mf.settings)
        self.export = build_export(mf, plan, settings)
        self.note.setText(note + f" Links start {settings.lead_in_for('default') / 1000:g} s before "
                          "each event (not before the start of its part).")
        self.chapters.setPlainText(self.export.description or
                                   "(No chapters: YouTube needs at least 3 chapters of 10 s or more.)")
        md = links_markdown(self.export.links, Path(mf.sources[0].path).stem if mf.sources else "Events")
        self.links.setPlainText(md)
        self.video_note.setText("" if mf.video_id else "links will have times only")
        removed = sum(i.code == "event_in_removed_region" for i in self.export.issues)
        other = [i.message for i in self.export.issues if i.code != "event_in_removed_region"]
        lines = []
        if removed:
            lines.append(f"{removed} event(s) are in removed footage and are not exported.")
        lines += other
        self.issues.setText("<br>".join(lines))

    def _video_entered(self) -> None:
        if self.session is None:
            return
        text = self.video.text().strip()
        vid = parse_video_id(text) if text else None
        if text and vid is None:
            self.video_note.setText("<span style='color:#c62828'>not a YouTube link or id</span>")
            return
        if vid != self.session.mf.video_id:
            self.session.set_video_id(vid)
            self.changed.emit()
            self.refresh()

    def copy_chapters(self) -> None:
        if self.export is not None and self.export.description:
            QGuiApplication.clipboard().setText(self.export.description)
            self.message.emit("Chapters copied")

    def save_links(self) -> list[Path]:
        if self.session is None or self.export is None:
            return []
        md_path, csv_path = links_paths(self.session.path)
        title = Path(self.session.mf.sources[0].path).stem if self.session.mf.sources else "Events"
        try:
            md_path.write_text(links_markdown(self.export.links, title), encoding="utf-8")
            csv_path.write_text(links_csv(self.export.links), encoding="utf-8-sig", newline="")
        except OSError as exc:
            self.message.emit(f"Could not save links: {exc}")
            return []
        self.message.emit(f"Saved {md_path.name} and {csv_path.name}")
        return [md_path, csv_path]
