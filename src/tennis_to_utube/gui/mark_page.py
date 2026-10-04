"""Step 2 — Mark: video, transport, score and event buttons, timeline, lists (DESIGN.md §10)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .. import matchfile, playback
from ..config import Config
from ..matchfile import MatchFile
from ..shortcuts import Shortcuts
from .actions import build_actions, key_text
from .player import PlayerBase, create_player


def _button(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space must never "click" a focused button
    if tip:
        b.setToolTip(tip)
    return b


class TransportBar(QWidget):
    """Skip/frame/play buttons, speed buttons (no keys, agreed) and the time."""

    def __init__(self, speeds: list[float], parent: QWidget | None = None):
        super().__init__(parent)
        self.buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for action_id, text in (("skip_back", "◀ 5s"), ("skip_back_short", "◀ 1s"),
                                ("frame_back", "◀ frame"), ("play_pause", "Play / Pause"),
                                ("frame_forward", "frame ▶"), ("skip_forward_short", "1s ▶"),
                                ("skip_forward", "5s ▶")):
            b = _button(text)
            self.buttons[action_id] = b
            layout.addWidget(b)
        layout.addSpacing(16)
        self.speed_group = QButtonGroup(self)
        self.speed_group.setExclusive(True)
        self.speed_buttons: dict[float, QPushButton] = {}
        for s in speeds:
            b = _button(f"{s:g}×", "Playback speed")
            b.setCheckable(True)
            self.speed_group.addButton(b)
            self.speed_buttons[s] = b
            layout.addWidget(b)
        layout.addStretch(1)
        self.time = QLabel("0:00:00.000")
        self.time.setStyleSheet("font-family: monospace; font-size: 15pt")
        layout.addWidget(self.time)

    def show_keys(self, shortcuts: Shortcuts) -> None:
        for action_id, b in self.buttons.items():
            keys = shortcuts.keys_for(action_id)
            base = b.text().split("  [")[0]
            b.setText(f"{base}  [{key_text(keys[0])}]" if keys else base)

    def show_speed(self, speed: float) -> None:
        for s, b in self.speed_buttons.items():
            b.setChecked(abs(s - speed) < 1e-6)


class MarkPage(QWidget):
    def __init__(self, config: Config, shortcuts: Shortcuts,
                 player_factory: Callable[[QWidget | None], PlayerBase] = create_player,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.shortcuts = shortcuts
        self.match: MatchFile | None = None
        self.match_path: Path | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.player = player_factory(self)
        speeds = [float(s) for s in config.get("playback.speeds")]
        self.transport = TransportBar(speeds)
        self.transport.show_keys(shortcuts)

        video_col = QVBoxLayout()
        video_col.addWidget(self.player, 1)
        video_col.addWidget(self.transport)
        left = QWidget()
        left.setLayout(video_col)

        # Filled in by later steps: score panel + event buttons, timeline, lists.
        self.side_panel = QWidget()
        self.side_panel.setMinimumWidth(420)
        self.side_layout = QVBoxLayout(self.side_panel)
        self.bottom = QWidget()
        self.bottom_layout = QVBoxLayout(self.bottom)
        self.bottom_layout.setContentsMargins(0, 0, 0, 0)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(left)
        top.addWidget(self.side_panel)
        top.setStretchFactor(0, 1)
        outer = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(top)
        outer.addWidget(self.bottom)
        outer.setStretchFactor(0, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(outer)

        skip_long = int(config.get("playback.skip_long_ms"))
        skip_short = int(config.get("playback.skip_short_ms"))
        handlers = {
            "play_pause": self.player.toggle_pause,
            "skip_back": lambda: self.player.skip(-skip_long),
            "skip_forward": lambda: self.player.skip(skip_long),
            "skip_back_short": lambda: self.player.skip(-skip_short),
            "skip_forward_short": lambda: self.player.skip(skip_short),
            "frame_back": lambda: self.player.step(-1),
            "frame_forward": lambda: self.player.step(1),
            "prev_event": lambda: self.jump_event(forward=False),
            "next_event": lambda: self.jump_event(forward=True),
        }
        self.actions = build_actions(self, shortcuts, handlers)
        for action_id, b in self.transport.buttons.items():
            b.clicked.connect(handlers[action_id])
        for s, b in self.transport.speed_buttons.items():
            b.clicked.connect(lambda _c=False, s=s: self.player.set_speed(s))
        self.player.positionChanged.connect(self._on_position)
        self.player.speedChanged.connect(self.transport.show_speed)
        self.transport.show_speed(1.0)

    # -- match -------------------------------------------------------------------

    def load_match(self, mf: MatchFile, path: Path) -> None:
        self.match, self.match_path = mf, Path(path)
        paths = [matchfile.resolve_source_path(s.path, path) for s in mf.sources]
        fps = mf.sources[0].fps if mf.sources else None
        self.player.load(paths, [s.duration_ms for s in mf.sources], fps)
        self.player.set_speed(1.0)
        self.setFocus()

    def position(self) -> int:
        return self.player.position_ms()

    def _on_position(self, t_ms: int) -> None:
        total = self.player.duration_ms
        self.transport.time.setText(f"{playback.clock_text(t_ms)} / {playback.clock_text(total)}")

    # -- navigation --------------------------------------------------------------

    def jump_event(self, forward: bool) -> None:
        if self.match is None:
            return
        t = playback.neighbor_event([e.t_ms for e in self.match.events], self.position(),
                                    self.player.frame, forward)
        if t is not None:
            self.player.set_paused(True)
            self.player.seek(t)

    def shutdown(self) -> None:
        self.player.shutdown()
