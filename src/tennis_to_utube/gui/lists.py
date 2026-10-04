"""Events and Issues lists under the timeline, the event edit dialog and the match dialog."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView,
    QLabel, QLineEdit, QTableView, QTabWidget, QWidget,
)

from .. import catalog, names, playback
from ..issues import Issue
from ..matchfile import Event
from ..scoring import PRESET_LABELS, Analysis, Step, score_text


def _who(e: Event, match: dict[str, Any]) -> str:
    if e.player:
        return e.player
    if e.side in ("A", "B"):
        return names.side_name(match, e.side)
    return ""


class EventsModel(QAbstractTableModel):
    HEADERS = ("Time", "Event", "Who", "Result", "Score after", "Note / tags")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.events: list[Event] = []
        self.steps: dict[str, Step] = {}
        self.match: dict[str, Any] = {}

    def set_data(self, events: list[Event], analysis: Analysis, match: dict[str, Any]) -> None:
        self.beginResetModel()
        self.events = sorted(events, key=lambda e: e.t_ms)
        self.steps = {st.event.id: st for st in analysis.steps}
        self.match = match
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.events)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def row_of(self, event_id: str | None) -> int:
        return next((i for i, e in enumerate(self.events) if e.id == event_id), -1)

    def _result(self, e: Event) -> tuple[str, str]:
        """(text, kind) with kind "entered" | "inferred" | "uncertain" | ""."""
        st = self.steps.get(e.id)
        if st is None:
            return "", ""
        if st.uncertain:
            return "?", "uncertain"
        if st.winner is None:
            return ("unknown", "uncertain") if e.result == "unknown" else ("", "")
        text = names.side_name(self.match, st.winner)
        return (text, "entered") if st.entered else (text, "inferred")

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        e = self.events[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return playback.clock_text(e.t_ms)
            if col == 1:
                shot = e.details.get("shot") if e.type == catalog.POINT else None
                return f"{catalog.label(e.type)} · {catalog.label(shot)}" if shot else catalog.label(e.type)
            if col == 2:
                return _who(e, self.match)
            if col == 3:
                text, kind = self._result(e)
                return f"{text} (inferred)" if kind == "inferred" else text
            if col == 4:
                st = self.steps.get(e.id)
                return score_text(st.view) if st else ""
            if col == 5:
                tags = f"[{', '.join(e.tags)}] " if e.tags else ""
                return tags + e.note
        if role == Qt.ItemDataRole.FontRole and col == 3:
            if self._result(e)[1] == "inferred":
                f = QFont()
                f.setItalic(True)
                return f
        if role == Qt.ItemDataRole.ForegroundRole and col == 3:
            kind = self._result(e)[1]
            if kind == "uncertain":
                return QColor("#c62828")
            if kind == "inferred":
                return QColor("#1565c0")
        return None


class IssuesModel(QAbstractTableModel):
    HEADERS = ("Time", "", "Issue")
    COLORS = {"error": "#c62828", "warning": "#ef6c00", "info": "#1565c0"}

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.issues: list[Issue] = []

    def set_issues(self, issues: list[Issue]) -> None:
        self.beginResetModel()
        self.issues = sorted(issues, key=lambda i: (i.t_ms is None, i.t_ms or 0))
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.issues)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        i = self.issues[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return [playback.clock_text(i.t_ms) if i.t_ms is not None else "", i.severity,
                    i.message][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 1:
            return QColor(self.COLORS.get(i.severity, "#000"))
        return None


def _table(model) -> QTableView:
    view = QTableView()
    view.setModel(model)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(22)
    view.horizontalHeader().setStretchLastSection(True)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys stay with the Mark page
    return view


class ListsPanel(QTabWidget):
    eventActivated = Signal(str)  # clicked: select + jump
    eventEditRequested = Signal(str)  # double-clicked
    timeActivated = Signal(int)  # an issue without an event

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.events_model = EventsModel(self)
        self.issues_model = IssuesModel(self)
        self.events_view = _table(self.events_model)
        self.issues_view = _table(self.issues_model)
        self.addTab(self.events_view, "Events")
        self.addTab(self.issues_view, "Issues")
        self._syncing = False
        self.events_view.clicked.connect(self._event_clicked)
        self.events_view.doubleClicked.connect(
            lambda idx: self.eventEditRequested.emit(self.events_model.events[idx.row()].id))
        self.issues_view.clicked.connect(self._issue_clicked)

    def set_data(self, events, analysis: Analysis, match, issues: list[Issue]) -> None:
        self.events_model.set_data(events, analysis, match)
        self.issues_model.set_issues(issues)
        warn = sum(i.severity != "info" for i in issues)
        self.setTabText(0, f"Events ({len(events)})")
        self.setTabText(1, f"Issues ({len(issues)})" + (f" — {warn} to check" if warn else ""))

    def select_event(self, event_id: str | None) -> None:
        row = self.events_model.row_of(event_id)
        self._syncing = True
        if row >= 0:
            self.events_view.selectRow(row)
            self.events_view.scrollTo(self.events_model.index(row, 0))
        else:
            self.events_view.clearSelection()
        self._syncing = False

    def _event_clicked(self, idx: QModelIndex) -> None:
        if not self._syncing:
            self.eventActivated.emit(self.events_model.events[idx.row()].id)

    def _issue_clicked(self, idx: QModelIndex) -> None:
        issue = self.issues_model.issues[idx.row()]
        if issue.event_id and self.events_model.row_of(issue.event_id) >= 0:
            self.eventActivated.emit(issue.event_id)
        elif issue.t_ms is not None:
            self.timeActivated.emit(issue.t_ms)


# -- dialogs -----------------------------------------------------------------------------


class _Dialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.form = QFormLayout(self)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

    def finish(self) -> None:
        self.form.addRow(self.error)
        self.form.addRow(self.buttons)

    def _accept(self) -> None:
        problem = self.validate()
        if problem:
            self.error.setText(problem)
        else:
            self.accept()

    def validate(self) -> str:
        return ""


class EventDialog(_Dialog):
    """Edit one event: time, type, side, player, result, tags, note."""

    def __init__(self, e: Event, match: dict[str, Any], parent: QWidget | None = None):
        super().__init__("Edit event", parent)
        self.time = QLineEdit(playback.clock_text(e.t_ms))
        self.type = QComboBox()
        types = list(catalog.LABELS) + ([e.type] if e.type not in catalog.LABELS else [])
        for t in types:
            self.type.addItem(catalog.label(t), t)
        self.type.setCurrentIndex(types.index(e.type))
        self.side = QComboBox()
        self.result = QComboBox()
        for combo, values in ((self.side, (None, "A", "B")), (self.result, (None, "A", "B", "unknown"))):
            for v in values:
                text = ("—" if v is None else "unknown" if v == "unknown" else
                        f"{v}: {names.side_name(match, v)}")
                combo.addItem(text, v)
        self.side.setCurrentIndex(max(0, self.side.findData(e.side)))
        self.result.setCurrentIndex(max(0, self.result.findData(e.result)))
        self.player = QComboBox()
        self.player.setEditable(True)
        self.player.addItem("", None)
        for side in "AB":
            for n in names.players(match, side):
                self.player.addItem(n, n)
        self.player.setCurrentText(e.player or "")
        self.tags = QLineEdit(", ".join(e.tags))
        self.tags.setPlaceholderText("e.g. close, bad miss")
        self.note = QLineEdit(e.note)
        for label, w in (("Time", self.time), ("Event", self.type), ("Side", self.side),
                         ("Player", self.player), ("Result", self.result), ("Tags", self.tags),
                         ("Note", self.note)):
            self.form.addRow(label, w)
        self.finish()

    def validate(self) -> str:
        return "" if playback.parse_clock(self.time.text()) is not None else "Time not understood"

    def changes(self) -> dict[str, Any]:
        return {
            "t_ms": playback.parse_clock(self.time.text()),
            "type": self.type.currentData(),
            "side": self.side.currentData(),
            "player": self.player.currentText().strip() or None,
            "result": self.result.currentData(),
            "tags": [t.strip() for t in self.tags.text().split(",") if t.strip()],
            "note": self.note.text(),
        }


class MatchDialog(_Dialog):
    """Players (renaming is a find/replace over the match file) and the match format."""

    def __init__(self, match: dict[str, Any], parent: QWidget | None = None):
        super().__init__("Match", parent)
        self.old = {side: names.players(match, side) for side in "AB"}
        self.edits: dict[str, list[QLineEdit]] = {}
        for side, label in (("A", "Side A (ours)"), ("B", "Side B")):
            self.edits[side] = [QLineEdit(n) for n in self.old[side]]
            for i, edit in enumerate(self.edits[side]):
                self.form.addRow(label if i == 0 else "", edit)
        fmt = match.get("format") or {}
        self.preset = QComboBox()
        for key, text in PRESET_LABELS.items():
            self.preset.addItem(text, key)
        if fmt.get("preset") in PRESET_LABELS:
            self.preset.setCurrentIndex(list(PRESET_LABELS).index(fmt["preset"]))
        self.no_ad = QCheckBox("No-ad")
        self.no_ad.setChecked(fmt.get("ad") is False)
        self.coman = QCheckBox("Coman tiebreak (change ends after point 1, then every 4)")
        self.coman.setChecked(fmt.get("tiebreak_changeovers") == "coman")
        self.form.addRow("Format", self.preset)
        self.form.addRow("", self.no_ad)
        self.form.addRow("", self.coman)
        self.form.addRow(QLabel("Renaming replaces the name everywhere in the match file."))
        self.finish()

    def renames(self) -> list[tuple[str, str]]:
        return [(old, edit.text().strip()) for side in "AB"
                for old, edit in zip(self.old[side], self.edits[side])
                if edit.text().strip() and edit.text().strip() != old]

    def validate(self) -> str:
        new = [e.text().strip() or o for side in "AB" for o, e in zip(self.old[side], self.edits[side])]
        return "Two players have the same name" if len(set(new)) != len(new) else ""

    def format_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {"preset": self.preset.currentData()}
        if self.no_ad.isChecked():
            spec["ad"] = False
        if self.coman.isChecked():
            spec["tiebreak_changeovers"] = "coman"
        return spec
