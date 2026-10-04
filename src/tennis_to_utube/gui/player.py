"""Video player widgets.

:class:`MpvPlayer` embeds mpv (python-mpv, libmpv) and plays the match's source files as
one joined timeline (an mpv EDL). :class:`NullPlayer` has the same interface without video
— used when libmpv is missing (it says so on screen) and in tests.

All times are integer ms on the joined timeline (see :mod:`..playback`).
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import playback


class PlayerBase(QWidget):
    positionChanged = Signal(int)  # ms; while playing (~30/s) and after every seek/step
    pausedChanged = Signal(bool)
    speedChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.frame = playback.frame_ms(None)
        self.duration_ms = 0
        self.setMinimumSize(640, 360)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # interface
    def load(self, paths: Sequence[Path], durations_ms: Sequence[int], fps: str | None) -> None:
        raise NotImplementedError

    def position_ms(self) -> int:
        raise NotImplementedError

    def is_paused(self) -> bool:
        raise NotImplementedError

    def speed(self) -> float:
        raise NotImplementedError

    def set_paused(self, paused: bool) -> None:
        raise NotImplementedError

    def set_speed(self, speed: float) -> None:
        raise NotImplementedError

    def seek(self, t_ms: int, precise: bool = True) -> None:
        """Show the frame on screen at ``t_ms`` (``precise=False``: nearest keyframe, fast)."""
        raise NotImplementedError

    def step(self, frames: int) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        pass

    # shared behaviour
    def toggle_pause(self) -> None:
        self.set_paused(not self.is_paused())

    def skip(self, delta_ms: int) -> None:
        self.seek(min(max(0, self.position_ms() + delta_ms), self.duration_ms))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt naming)
        """Mouse wheel over the video steps frames, only while paused."""
        if self.is_paused():
            steps = event.angleDelta().y() // 120
            if steps:
                self.step(steps)  # wheel forward (up) = next frame (owner's choice)
            event.accept()
        else:
            event.ignore()


class NullPlayer(PlayerBase):
    """No video: a clock that behaves like the player (seeking, speed, frame steps)."""

    def __init__(self, reason: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._base_ms = 0
        self._paused = True
        self._speed = 1.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(lambda: self.positionChanged.emit(self.position_ms()))
        label = QLabel(reason or "No video")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet("background: #111; color: #ccc; padding: 24px")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

    def load(self, paths, durations_ms, fps) -> None:
        self.duration_ms = sum(durations_ms)
        self.frame = playback.frame_ms(fps)
        self._base_ms = 0
        self.set_paused(True)
        self.positionChanged.emit(0)

    def position_ms(self) -> int:
        if self._paused:
            return self._base_ms
        t = self._base_ms + round(self._clock.elapsed() * self._speed)
        return min(t, self.duration_ms)

    def is_paused(self) -> bool:
        return self._paused

    def speed(self) -> float:
        return self._speed

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._base_ms = self.position_ms()
        self._paused = paused
        if paused:
            self._timer.stop()
        else:
            self._clock.restart()
            self._timer.start()
        self.pausedChanged.emit(paused)

    def set_speed(self, speed: float) -> None:
        self._base_ms = self.position_ms()
        self._clock.restart()
        self._speed = speed
        self.speedChanged.emit(speed)

    def seek(self, t_ms: int, precise: bool = True) -> None:
        self._base_ms = min(max(0, int(t_ms)), self.duration_ms)
        self._clock.restart()
        self.positionChanged.emit(self.position_ms())

    def step(self, frames: int) -> None:
        self.set_paused(True)
        # land inside the neighbouring frame, as mpv does
        start = math.floor(self._base_ms / self.frame + 1e-6) * self.frame  # 10010/16.68 = 599.99…
        self.seek(max(0, math.ceil(start + frames * self.frame)))


def _add_dll_dirs() -> None:
    """Let python-mpv find libmpv-2.dll next to run.bat (the working dir) on Windows."""
    if sys.platform != "win32":
        return
    for d in {Path.cwd(), Path(__file__).resolve().parents[3]}:
        if (d / "libmpv-2.dll").exists():
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(d))


class MpvPlayer(PlayerBase):
    def __init__(self, parent: QWidget | None = None, **mpv_options):
        super().__init__(parent)
        _add_dll_dirs()
        import mpv  # noqa: PLC0415 (optional dependency, imported when used)

        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setStyleSheet("background: black")
        self._mpv_module = mpv
        self._options = dict(
            keep_open="always", pause=True, hr_seek="yes", hr_seek_framedrop="no",
            hwdec="auto-safe", osc=False, input_default_bindings=False, input_vo_keyboard=False,
            input_cursor=False, cursor_autohide="no",
        )
        self._options.update(mpv_options)
        self.mpv = None  # created on first load, once the widget sits in its final window
        self._paused = True
        self._last = -1
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _ensure_mpv(self):
        if self.mpv is None:
            # winId() only now: a native window id can change when a widget is reparented
            self.mpv = self._mpv_module.MPV(wid=str(int(self.winId())), **self._options)
        return self.mpv

    def _poll(self) -> None:
        if self.mpv is None:
            return
        pos = self.position_ms()
        if pos != self._last:
            self._last = pos
            self.positionChanged.emit(pos)
        paused = bool(self.mpv.pause)
        if paused != self._paused:
            self._paused = paused
            self.pausedChanged.emit(paused)

    def load(self, paths, durations_ms, fps) -> None:
        self.duration_ms = sum(durations_ms)
        self.frame = playback.frame_ms(fps)
        player = self._ensure_mpv()
        player.pause = True
        player.play(playback.edl_url(paths, durations_ms))

    def position_ms(self) -> int:
        try:
            return playback.position_ms(self.mpv.time_pos)
        except Exception:  # mpv not created or nothing loaded yet
            return 0

    def is_paused(self) -> bool:
        return self.mpv is None or bool(self.mpv.pause)

    def speed(self) -> float:
        return 1.0 if self.mpv is None else float(self.mpv.speed)

    def set_paused(self, paused: bool) -> None:
        if self.mpv is not None:
            self.mpv.pause = paused

    def set_speed(self, speed: float) -> None:
        if self.mpv is not None:
            self.mpv.speed = speed
        self.speedChanged.emit(speed)

    def seek(self, t_ms: int, precise: bool = True) -> None:
        target = playback.seek_seconds(int(t_ms), self.frame) if precise else max(0, t_ms) / 1000
        try:
            self.mpv.command("seek", f"{target:.4f}", "absolute+exact" if precise else "absolute+keyframes")
        except Exception:
            return  # nothing loaded

    def step(self, frames: int) -> None:
        if self.mpv is None:
            return
        command = "frame-step" if frames > 0 else "frame-back-step"
        for _ in range(abs(frames)):
            self.mpv.command(command)

    def shutdown(self) -> None:
        self._timer.stop()
        if self.mpv is not None:
            self.mpv.terminate()
            self.mpv = None


def create_player(parent: QWidget | None = None) -> PlayerBase:
    """mpv if libmpv can be loaded, else a NullPlayer that explains what is missing."""
    try:
        return MpvPlayer(parent)
    except (ImportError, OSError) as exc:
        return NullPlayer(
            "Video playback needs libmpv.\n\nOn Windows put libmpv-2.dll next to run.bat "
            "(see README). Marking still works without video.\n\n" + str(exc), parent)
