"""Run slow work (ffprobe, ffmpeg) off the GUI thread."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

Report = Callable[[float, str], None]  # fraction (or -1 if unknown), what is happening


class Job(QThread):
    progressed = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Report], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(lambda fraction, text: self.progressed.emit(fraction, text))
        except Exception as exc:  # shown to the user
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(result)
