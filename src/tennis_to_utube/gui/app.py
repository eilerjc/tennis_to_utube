"""Application entry point: ``python -m tennis_to_utube [folder or match file]``, or
``python -m tennis_to_utube --cut video…`` to open the videos straight in Cut only."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .. import sources
from ..appstate import load_state
from ..config import load_config
from ..probe import Tools, ToolError, probe
from ..shortcuts import load_shortcuts
from .main_window import APP_TITLE, MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName(APP_TITLE)
    config = load_config()
    state = load_state()
    window = MainWindow(config, state, load_shortcuts())
    cut_videos = [Path(a) for a in argv[2:]] if len(argv) > 1 and argv[1] == "--cut" else None
    target = Path(argv[1]) if len(argv) > 1 and cut_videos is None else None
    if cut_videos:
        window.files.go_to(cut_videos[0].parent)
        open_cut_videos(window, cut_videos, Tools.from_config(config))
    elif target is not None and target.is_file():
        window.files.go_to(target.parent)
        window.files.open_match(target)
    elif target is not None and target.is_dir():
        window.files.go_to(target)
    elif state.last_folder and Path(state.last_folder).is_dir():
        window.files.go_to(state.last_folder)  # (also cut.bat without videos: pick them here)
    window.place_window()
    window.show()  # not maximized (owner's choice); size and place are remembered
    return app.exec()


def open_cut_videos(window: MainWindow, paths: list[Path], tools: Tools) -> bool:
    """Cut only with these videos (e.g. dropped on cut.bat), put in GoPro order."""
    ordered = sources.order_files(p for p in paths if p.is_file())
    try:
        infos = [probe(p, tools) for p in ordered]
    except ToolError as exc:
        QMessageBox.warning(window, "Cannot read the videos", str(exc))
        return False
    errors = sources.check_join_compatible(infos)
    if not infos or errors:
        QMessageBox.warning(window, "Cannot join losslessly",
                            "\n".join(i.message for i in errors) or "No videos given.")
        return False
    window.open_cut(infos)
    return True
