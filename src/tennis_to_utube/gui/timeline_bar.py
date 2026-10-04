"""Timeline bars (DESIGN.md §10): the whole-match overview and the zoomable detail strip.

Drag = scrub only (fast keyframe seeks while dragging, exact seek on release). Click on a
tick selects it and jumps there, never moves it. Ctrl+drag moves an event (ghost at the
original position; Esc cancels). Wheel zooms the detail strip around the cursor.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QPolygonF, QWheelEvent
from PySide6.QtWidgets import QToolTip, QWidget

from .. import catalog, playback
from ..issues import Issue
from ..matchfile import Event
from ..timeline_view import Band, View, category, nearest_event

COLORS = {
    "point": "#43a047", "serve": "#1e88e5", "shot": "#8e24aa", "coaching": "#00897b",
    "note": "#f9a825", "game": "#fb8c00", "set": "#6d4c41", "match": "#3949ab",
    "other": "#757575",
}
POINT_COLORS = {"A": "#43a047", "B": "#e53935", "unknown": "#9e9e9e"}
BAND_COLORS = {"set": ("#d7ccc8", "#bcaaa4"), "game": ("#ffe0b2", "#ffcc80")}
PICK_PX = 5


def tick_color(e: Event) -> QColor:
    if e.type == catalog.POINT:
        return QColor(POINT_COLORS.get(e.result or "unknown", "#9e9e9e"))
    return QColor(COLORS.get(category(e), COLORS["other"]))


class TimelineBar(QWidget):
    seekRequested = Signal(int, bool)  # t_ms, precise
    eventClicked = Signal(str)
    eventMoved = Signal(str, int)
    viewChanged = Signal()
    message = Signal(str)

    def __init__(self, zoomable: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.zoomable = zoomable
        self.view = View(0, 1, 1)
        self.events: list[Event] = []
        self.bands: list[Band] = []
        self.cuts: list[tuple[int, int]] = []
        self.issues: list[Issue] = []
        self.position = 0
        self.selected: str | None = None
        self.locked = False
        self.window_marker: tuple[float, float] | None = None  # overview: detail range
        self._mode: str | None = None  # "scrub" | "move"
        self._move: tuple[str, int, int] | None = None  # id, original t, current t
        self.setMouseTracking(True)
        self.setMinimumHeight(70 if zoomable else 44)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # -- data ----------------------------------------------------------------------

    def set_data(self, events: Sequence[Event], bands: Sequence[Band], cuts, issues,
                 total_ms: int) -> None:
        self.events = sorted(events, key=lambda e: e.t_ms)
        self.bands = list(bands)
        self.cuts = list(cuts)
        self.issues = [i for i in issues if i.t_ms is not None]
        if self.view.total_ms != total_ms:
            self.view = View(0, total_ms if not self.zoomable else min(total_ms, 120_000), total_ms)
            self.view.clamp()
            self.viewChanged.emit()
        self.update()

    def set_position(self, t_ms: int, follow: bool = False) -> None:
        self.position = t_ms
        if follow and self.zoomable and self._mode is None and self.view.follow(t_ms):
            self.viewChanged.emit()
        self.update()

    # -- drawing -------------------------------------------------------------------

    def _x(self, t: float) -> float:
        return self.view.x_of(t, self.width())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#fafafa"))
        band_h = 10 if self.zoomable else 7
        for b in self.bands:
            y = 0 if b.kind == "set" else band_h
            x0, x1 = self._x(b.start_ms), self._x(b.end_ms)
            if x1 < 0 or x0 > w:
                continue
            p.fillRect(QRectF(x0, y, max(1.0, x1 - x0), band_h),
                       QColor(BAND_COLORS[b.kind][b.index % 2]))
            if self.zoomable and x1 - x0 > 30:
                p.setPen(QColor("#5d4037"))
                p.drawText(QRectF(x0 + 2, y - 2, x1 - x0, band_h + 4), b.label)
        tick_top = 2 * band_h + 2
        if self.zoomable:
            self._draw_grid(p, tick_top)
        for start, end in self.cuts:
            x0, x1 = self._x(start), self._x(end)
            if x1 >= 0 and x0 <= w:
                p.fillRect(QRectF(x0, 0, max(1.0, x1 - x0), h), QColor(80, 80, 80, 70))
        moving = self._move[0] if self._move else None
        for e in self.events:
            x = self._x(self._move[2] if e.id == moving else e.t_ms)
            if x < -2 or x > w + 2:
                continue
            selected = e.id == self.selected
            p.setPen(QPen(tick_color(e), 3 if selected else 1.5))
            p.drawLine(QPointF(x, tick_top), QPointF(x, h - (14 if self.zoomable else 2)))
            if selected:
                p.setPen(QPen(QColor("black"), 1))
                p.drawRect(QRectF(x - 3, tick_top, 6, 6))
        if self._move:
            p.setPen(QPen(QColor(0, 0, 0, 90), 1, Qt.PenStyle.DashLine))
            x = self._x(self._move[1])
            p.drawLine(QPointF(x, tick_top), QPointF(x, h))
        p.setBrush(QColor("#d32f2f"))
        p.setPen(Qt.PenStyle.NoPen)
        for issue in self.issues:
            x = self._x(issue.t_ms)
            if 0 <= x <= w:
                p.drawPolygon(QPolygonF([QPointF(x - 4, h - 1), QPointF(x + 4, h - 1),
                                         QPointF(x, h - 8)]))
        if self.window_marker:
            x0, x1 = self._x(self.window_marker[0]), self._x(self.window_marker[1])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#1565c0"), 1.5))
            p.drawRect(QRectF(x0, 0.5, max(2.0, x1 - x0), h - 1))
        x = self._x(self.position)
        p.setPen(QPen(QColor("#d32f2f"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.end()

    def _draw_grid(self, p: QPainter, top: int) -> None:
        w, h = self.width(), self.height()
        steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
        step_ms = next((s * 1000 for s in steps if s * 1000 / self.view.span_ms * w >= 90), 3_600_000)
        t = (int(self.view.start_ms) // step_ms) * step_ms
        p.setPen(QColor("#9e9e9e"))
        while t <= self.view.start_ms + self.view.span_ms:
            x = self._x(t)
            if x >= 0:
                p.drawLine(QPointF(x, h - 14), QPointF(x, h - 10))
                p.drawText(QPointF(x + 2, h - 2), playback.clock_text(t).split(".")[0])
            t += step_ms

    # -- mouse ---------------------------------------------------------------------

    def _hit(self, x: float) -> Event | None:
        tol = PICK_PX / max(1, self.width()) * self.view.span_ms
        return nearest_event(self.events, self.view.t_of(x, self.width()), tol)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        hit = self._hit(x)
        if hit is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.locked:
                self.message.emit("Events are locked (Lock events to unlock)")
                return
            self._mode = "move"
            self._move = (hit.id, hit.t_ms, hit.t_ms)
            self.setFocus()
        elif hit is not None:
            self.eventClicked.emit(hit.id)
        else:
            self._mode = "scrub"
            self.seekRequested.emit(self.view.t_of(x, self.width()), False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        x = event.position().x()
        t = self.view.t_of(x, self.width())
        if self._mode == "scrub":
            self.seekRequested.emit(t, False)
        elif self._mode == "move" and self._move:
            self._move = (self._move[0], self._move[1], t)
            self.update()
        else:
            hit = self._hit(x)
            text = playback.clock_text(t)
            if hit is not None:
                text += f"\n{catalog.label(hit.type)}" + (f" — {hit.result}" if hit.result else "")
                if hit.note:
                    text += f": {hit.note}"
            QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        t = self.view.t_of(event.position().x(), self.width())
        if self._mode == "scrub":
            self.seekRequested.emit(t, True)
        elif self._mode == "move" and self._move:
            event_id, original, _ = self._move
            if t != original:
                self.eventMoved.emit(event_id, t)
        self._mode = None
        self._move = None
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self._mode == "move":
            self._mode = None
            self._move = None
            self.update()
            self.message.emit("Move cancelled")
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self.zoomable:
            event.ignore()
            return
        notches = event.angleDelta().y() / 120
        if notches:
            anchor = self.view.t_of(event.position().x(), self.width())
            self.view.zoom(1.25 ** notches, anchor)
            self.viewChanged.emit()
            self.update()
        event.accept()
