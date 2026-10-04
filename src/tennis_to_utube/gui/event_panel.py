"""Right-hand panel of the Mark step: score display and event buttons, plus the small
dialogs some events need (Set score, Note, Rules change, Ending state)."""

from __future__ import annotations

import html
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from .. import names
from ..flow import Flow
from ..scoring import PRESET_LABELS, parse_pair, parse_points, parse_sets, points_text
from ..shortcuts import ACTIONS_BY_ID, Shortcuts
from .actions import call, key_text

# Button rows, mirroring the keyboard rows (DESIGN.md §11).
BUTTON_ROWS: list[tuple[str, list[str]]] = [
    ("Points", ["point_a", "point_b", "point_unknown"]),
    ("Serve", ["serve_in", "fault", "let", "ace", "set_server"]),
    ("Shots (end the point)", ["winner_a", "forced_error_a", "unforced_error_a",
                               "winner_b", "forced_error_b", "unforced_error_b"]),
    ("Games", ["game_start", "game_start_other_server", "game_end_a", "game_end_b",
               "game_end_unknown"]),
    ("Sets", ["set_start", "score_state", "set_end_a", "set_end_b", "set_end_unknown"]),
    ("Coaching", ["good_recovery", "footwork", "body_language", "late_contact", "strategy",
                  "note"]),
    ("Match", ["match_start", "match_end", "tiebreak_start", "rules_change", "ending_state"]),
    ("Edit", ["undo", "redo", "delete_event", "nudge_back", "nudge_forward", "lock_events"]),
]
COLUMNS = 3
# Shorter button labels where the action label is long (menus keep the full label).
BUTTON_LABELS = {"nudge_back": "Event ◀ 1 frame", "nudge_forward": "Event 1 frame ▶",
                 "delete_event": "Delete event", "lock_events": "Lock events",
                 "game_start_other_server": "Game start (other)"}

SUGGESTED_STYLE = "QPushButton { background: #2e7d32; color: white; font-weight: bold; }"


class ScorePanel(QLabel):
    """Score at the playhead; parts that are not certain show as "?"."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setStyleSheet("font-size: 14pt; padding: 6px")

    def setText(self, text: str) -> None:  # noqa: N802 (skip identical updates: called ~30/s)
        if text != self.text():
            super().setText(text)

    def show_flow(self, flow: Flow, match: dict[str, Any]) -> None:
        v = flow.score
        if flow.match_over:
            winner = names.side_name(match, v.winner)
            heading = f"Match won by {html.escape(winner)}"
        else:
            set_no = len(v.sets) + 1 if v.sets is not None else None
            stage = "Tiebreak" if v.in_tiebreak else "Game in progress" if flow.in_game else "Between games"
            heading = (f"Set {set_no} · " if set_no else "") + stage
        rows = []
        for i, side in enumerate("AB"):
            serving = "●" if v.server == side and not flow.match_over else ""
            sets = "".join(
                f"<td align='center'>{s.games[i] if s.games else '?'}"
                f"{'<sup>' + str(s.tiebreak[i]) + '</sup>' if s.tiebreak and s.games not in ((1, 0), (0, 1)) else ''}</td>"
                for s in (v.sets or ()))
            if v.sets is None:
                sets = "<td>?</td>"
            games = "" if flow.match_over else f"<td align='center'><b>{v.games[i] if v.games else '?'}</b></td>"
            pts = ""
            if not flow.match_over:
                text = points_text(v)
                pts = f"<td align='center'>{text.split('–')[i] if text else '?'}</td>"
            rows.append(f"<tr><td style='color:#2e7d32'>{serving}</td>"
                        f"<td>{html.escape(names.side_name(match, side))}</td>{sets}{games}{pts}</tr>")
        uncertain = "" if v.certain else "<br><small>(score partly uncertain — see Issues)</small>"
        if v.server is None and not flow.match_over:
            uncertain += ("<br><small style='color:#b35c00'>Server not set — G asks, or "
                          "Set server… in Serve</small>")
        self.setText(f"<b>{heading}</b><table cellspacing='8'>{''.join(rows)}</table>{uncertain}")


class EventButtons(QWidget):
    """One button per action, grouped in the keyboard's rows; suggested ones highlighted."""

    def __init__(self, shortcuts: Shortcuts, handlers: dict[str, Callable[[], None]],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.shortcuts = shortcuts
        self.buttons: dict[str, QPushButton] = {}
        self._suggested: set[str] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for title, ids in BUTTON_ROWS:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setSpacing(4)
            for n, action_id in enumerate(ids):
                b = QPushButton()
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setMinimumHeight(40)
                if action_id in handlers:
                    b.clicked.connect(call(handlers[action_id]))
                self.buttons[action_id] = b
                grid.addWidget(b, n // COLUMNS, n % COLUMNS)
            layout.addWidget(box)
        layout.addStretch(1)
        self.set_names({"A": "A", "B": "B"})

    def set_names(self, short_names: dict[str, str]) -> None:
        for action_id, b in self.buttons.items():
            keys = self.shortcuts.keys_for(action_id)
            label = BUTTON_LABELS.get(action_id) or ACTIONS_BY_ID[action_id].button_text(short_names)
            b.setText(f"{label}\n[{key_text(keys[0])}]" if keys else label)

    def set_suggested(self, action_ids: list[str]) -> None:
        wanted = set(action_ids)
        if wanted == self._suggested:
            return  # called ~30/s while playing; restyling is not free
        self._suggested = wanted
        for action_id, b in self.buttons.items():
            b.setStyleSheet(SUGGESTED_STYLE if action_id in wanted else "")


# -- dialogs --------------------------------------------------------------------------


class _Form(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.form = QFormLayout(self)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons

    def finish_layout(self) -> None:
        self.form.addRow(self.error)
        self.form.addRow(self._buttons)

    def _accept(self) -> None:
        problem = self.validate()
        if problem:
            self.error.setText(problem)
        else:
            self.accept()

    def validate(self) -> str:
        return ""


class ScoreStateDialog(_Form):
    """Set score: only the parts the user knows. Scores are typed as on a scoreboard."""

    def __init__(self, match: dict[str, Any], in_tiebreak: bool = False,
                 parent: QWidget | None = None, server: str | None = None):
        super().__init__("Set score", parent)
        self.in_tiebreak = in_tiebreak
        self.sets = QLineEdit()
        self.sets.setPlaceholderText("completed sets, e.g. 6-4 3-6 (empty = unknown)")
        self.games = QLineEdit()
        self.games.setPlaceholderText("this set, e.g. 3-2")
        self.points = QLineEdit()
        self.points.setPlaceholderText("this game, e.g. 30-40, AD-40, deuce" + (", 5-3" if in_tiebreak else ""))
        self.server = QComboBox()
        self.server.addItem("(unknown)", None)
        for side in "AB":
            for n in names.players(match, side):
                self.server.addItem(n, n)
        if server is not None and self.server.findData(server) >= 0:
            self.server.setCurrentIndex(self.server.findData(server))  # who serves now
        self.no_sets = QCheckBox("No completed sets yet (first set)")
        self.form.addRow("Sets", self.sets)
        self.form.addRow("", self.no_sets)
        self.form.addRow("Games", self.games)
        self.form.addRow("Points", self.points)
        self.form.addRow("Server", self.server)
        self.finish_layout()

    def details(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.no_sets.isChecked():
            d["sets"] = []
        elif self.sets.text().strip():
            d["sets"] = [list(p) for p in parse_sets(self.sets.text()) or []]
        if self.games.text().strip():
            d["games"] = list(parse_pair(self.games.text()) or [])
        if self.points.text().strip():
            d["points"] = list(parse_points(self.points.text(), self.in_tiebreak) or [])
        if self.server.currentData():
            d["server"] = self.server.currentData()
        return d

    def validate(self) -> str:
        if self.sets.text().strip() and not self.no_sets.isChecked() and parse_sets(self.sets.text()) is None:
            return "Sets not understood (use e.g. 6-4 3-6)"
        if self.games.text().strip() and parse_pair(self.games.text()) is None:
            return "Games not understood (use e.g. 3-2)"
        if self.points.text().strip() and parse_points(self.points.text(), self.in_tiebreak) is None:
            return "Points not understood (use e.g. 30-40, AD-40, deuce)"
        if not self.details():
            return "Enter at least one part of the score"
        return ""


class RulesDialog(_Form):
    def __init__(self, current: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__("Rules change (from here on)", parent)
        self.preset = QComboBox()
        for key, label in PRESET_LABELS.items():
            self.preset.addItem(label, key)
        self.no_ad = QCheckBox("No-ad")
        self.coman = QCheckBox("Coman tiebreak (change ends after point 1, then every 4)")
        self.match_tiebreak = QCheckBox("Deciding set is a 10-point match tiebreak")
        current = current or {}
        if current.get("preset") in PRESET_LABELS:
            self.preset.setCurrentIndex(list(PRESET_LABELS).index(current["preset"]))
        self.no_ad.setChecked(current.get("ad") is False)
        self.coman.setChecked(current.get("tiebreak_changeovers") == "coman")
        self.form.addRow("Format", self.preset)
        self.form.addRow("", self.no_ad)
        self.form.addRow("", self.coman)
        self.form.addRow("", self.match_tiebreak)
        self.finish_layout()

    def details(self) -> dict[str, Any]:
        fmt: dict[str, Any] = {"preset": self.preset.currentData(), "ad": not self.no_ad.isChecked(),
                               "tiebreak_changeovers": "coman" if self.coman.isChecked() else "regular"}
        if self.match_tiebreak.isChecked():
            fmt["final_set"] = "match_tiebreak"
        return {"format": fmt}


class EndingStateDialog(_Form):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Ending state (final score from the scorebook)", parent)
        self.sets = QLineEdit()
        self.sets.setPlaceholderText("e.g. 6-4 3-6 [10-8]")
        self.form.addRow("Final sets", self.sets)
        self.finish_layout()

    def details(self) -> dict[str, Any]:
        return {"sets": [list(p) for p in parse_sets(self.sets.text()) or []], "entered": True}

    def validate(self) -> str:
        return "" if parse_sets(self.sets.text()) else "Enter the set scores, e.g. 6-4 3-6"
