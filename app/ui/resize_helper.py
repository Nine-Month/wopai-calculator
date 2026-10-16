"""Edge/corner resize for frameless windows (~8px hit margin + event filter)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QWidget

_MARGIN = 8

# Bit flags for edges
_L, _R, _T, _B = 1, 2, 4, 8

_CURSORS = {
    _L: Qt.SizeHorCursor,
    _R: Qt.SizeHorCursor,
    _T: Qt.SizeVerCursor,
    _B: Qt.SizeVerCursor,
    _L | _T: Qt.SizeFDiagCursor,
    _R | _B: Qt.SizeFDiagCursor,
    _R | _T: Qt.SizeBDiagCursor,
    _L | _B: Qt.SizeBDiagCursor,
}


class ResizeHelper(QObject):
    """Install on a frameless QWidget; call from its mouse/event handlers.

    Also installs an event filter on the window so child widgets do not eat
    edge hits (frameless resize stays usable).
    """

    def __init__(self, widget: QWidget, margin: int = _MARGIN, *, enabled: bool = False):
        super().__init__(widget)
        self.w = widget
        self.margin = margin
        self.enabled = bool(enabled)
        self._edges = 0
        self._press_global: QPoint | None = None
        self._press_geo: QRect | None = None
        self._resizing = False
        self._filter_installed = False
        # Clear any accidental fixed-size lock so the window can grow/shrink
        try:
            self.w.setMinimumSize(max(self.w.minimumWidth(), 260), max(self.w.minimumHeight(), 400))
            from PySide6.QtWidgets import QWIDGETSIZE_MAX

            self.w.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        except Exception:
            pass
        if self.enabled:
            self.install_filter()

    def install_filter(self):
        if self._filter_installed:
            return
        self.w.installEventFilter(self)
        self._filter_installed = True

    def eventFilter(self, obj, event):
        if not self.enabled or obj is not self.w:
            return False
        et = event.type()
        if et == QEvent.MouseMove:
            if self._resizing:
                self.move(event.globalPosition().toPoint())
                return True
            self.update_cursor(event.position().toPoint())
        elif et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self.begin(event.globalPosition().toPoint(), event.position().toPoint()):
                return True
        elif et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self.end():
                return True
        elif et == QEvent.Leave:
            if not self._resizing:
                self.w.unsetCursor()
        return False

    def _hit(self, pos: QPoint) -> int:
        r = self.w.rect()
        m = self.margin
        e = 0
        if pos.x() <= m:
            e |= _L
        elif pos.x() >= r.width() - m:
            e |= _R
        if pos.y() <= m:
            e |= _T
        elif pos.y() >= r.height() - m:
            e |= _B
        return e

    def update_cursor(self, pos: QPoint):
        if not self.enabled:
            self.w.unsetCursor()
            return
        if self._resizing:
            return
        if self.w.isMaximized():
            self.w.unsetCursor()
            return
        e = self._hit(pos)
        if e:
            self.w.setCursor(_CURSORS.get(e, Qt.ArrowCursor))
        else:
            self.w.unsetCursor()

    def begin(self, global_pos: QPoint, local_pos: QPoint) -> bool:
        if not self.enabled:
            return False
        if self.w.isMaximized():
            return False
        e = self._hit(local_pos)
        if not e:
            return False
        handle = self.w.windowHandle()
        if handle is not None:
            edge = self._qt_edge(e)
            if edge is not None:
                try:
                    handle.startSystemResize(edge)
                    self._edges = e
                    # Native resize: mark briefly so callers can pair drag start/end
                    self._resizing = True
                    self._press_global = None
                    self._press_geo = None
                    return True
                except Exception:
                    pass
        self._edges = e
        self._press_global = QPoint(global_pos)
        self._press_geo = QRect(self.w.geometry())
        self._resizing = True
        return True

    def _qt_edge(self, e: int):
        edges = Qt.Edges()
        if e & _L:
            edges |= Qt.LeftEdge
        if e & _R:
            edges |= Qt.RightEdge
        if e & _T:
            edges |= Qt.TopEdge
        if e & _B:
            edges |= Qt.BottomEdge
        return edges if int(edges) else None

    def move(self, global_pos: QPoint) -> bool:
        if not self._resizing or self._press_global is None or self._press_geo is None:
            return False
        delta = global_pos - self._press_global
        geo = QRect(self._press_geo)
        min_w = max(self.w.minimumWidth(), 200)
        min_h = max(self.w.minimumHeight(), 300)

        if self._edges & _L:
            new_left = geo.left() + delta.x()
            if geo.right() - new_left + 1 >= min_w:
                geo.setLeft(new_left)
        if self._edges & _R:
            new_right = geo.right() + delta.x()
            if new_right - geo.left() + 1 >= min_w:
                geo.setRight(new_right)
        if self._edges & _T:
            new_top = geo.top() + delta.y()
            if geo.bottom() - new_top + 1 >= min_h:
                geo.setTop(new_top)
        if self._edges & _B:
            new_bottom = geo.bottom() + delta.y()
            if new_bottom - geo.top() + 1 >= min_h:
                geo.setBottom(new_bottom)

        self.w.setGeometry(geo)
        return True

    def end(self) -> bool:
        if not self._resizing:
            return False
        self._resizing = False
        self._edges = 0
        self._press_global = None
        self._press_geo = None
        return True

    @property
    def active(self) -> bool:
        return self._resizing
