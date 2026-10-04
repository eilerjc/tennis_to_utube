"""Application entry point: ``python -m tennis_to_utube [folder or match file]``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ..appstate import load_state
from ..config import load_config
from ..shortcuts import load_shortcuts
from .main_window import APP_TITLE, MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName(APP_TITLE)
    config = load_config()
    state = load_state()
    window = MainWindow(config, state, load_shortcuts())
    target = Path(argv[1]) if len(argv) > 1 else None
    if target is not None and target.is_file():
        window.files.go_to(target.parent)
        window.files.open_match(target)
    elif target is not None and target.is_dir():
        window.files.go_to(target)
    elif state.last_folder and Path(state.last_folder).is_dir():
        window.files.go_to(state.last_folder)
    window.showMaximized()
    return app.exec()
