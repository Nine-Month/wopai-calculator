"""Custom frameless title bar with pin + settings."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget


class TitleBar(QWidget):
    pin_toggled = Signal(bool)
    settings_clicked = Signal()
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    # Emitted around system/manual window move so main window can drop acrylic
    drag_started = Signal()
    drag_ended = Signal()

    def __init__(self, parent=None, *, show_min=True, show_max=False, show_pin=True, show_settings=True):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(32)
        self._drag_pos: QPoint | None = None
        self._dragging = False
        self._pinned = False
        self._show_max = show_max
        self._poll_timer: QTimer | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 2, 0)
        lay.setSpacing(1)

        self.title = QLabel("窝牌计算器 Ver 1.0.0")
        self.title.setObjectName("TitleLabel")
        self.title.setMinimumWidth(40)
        lay.addWidget(self.title, 1)

        self.btn_pin = self._btn("📌", "窗口置顶")
        self.btn_pin.setObjectName("PinBtn")
        self.btn_pin.setCheckable(True)
        self.btn_pin.clicked.connect(self._on_pin)
        self.btn_settings = self._btn("⚙", "程序设置")
        self.btn_settings.clicked.connect(self.settings_clicked.emit)
        self.btn_min = self._btn("─", "最小化")
        self.btn_min.clicked.connect(self.minimize_clicked.emit)
        self.btn_max = self._btn("□", "最大化")
        self.btn_max.clicked.connect(self.maximize_clicked.emit)
        self.btn_close = self._btn("×", "关闭")
        self.btn_close.setObjectName("CloseBtn")
        self.btn_close.clicked.connect(self.close_clicked.emit)

        for b, show in (
            (self.btn_pin, show_pin),
            (self.btn_settings, show_settings),
            (self.btn_min, show_min),
            (self.btn_max, show_max),
            (self.btn_close, True),
        ):
            if show:
                lay.addWidget(b, 0)
            else:
                b.hide()

        self._refresh_pin_look()

    def set_title_text(self, text: str):
        self.title.setText(text)

    def _btn(self, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(28, 26)
        b.setMinimumSize(28, 26)
        b.setMaximumSize(28, 26)
        b.setAutoRaise(True)
        b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        b.setStyleSheet("QToolButton { padding: 0; margin: 0; font-size: 13px; }")
        return b

    def set_pinned(self, pinned: bool):
        self._pinned = pinned
        self.btn_pin.setChecked(pinned)
        self._refresh_pin_look()

    def _refresh_pin_look(self):
        if self._pinned:
            self.btn_pin.setText("📌")
            self.btn_pin.setStyleSheet(
                "QToolButton { padding: 0; margin: 0; font-size: 13px; "
                "background-color: rgba(180,140,40,200); color: #ffe9a0; "
                "border: 1px solid rgba(255,220,100,0.5); border-radius: 4px; }"
            )
        else:
            self.btn_pin.setText("📌")
            self.btn_pin.setStyleSheet(
                "QToolButton { padding: 0; margin: 0; font-size: 13px; "
                "background-color: rgba(55,55,62,200); color: #c8c8d0; "
                "border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; }"
                "QToolButton:hover { background-color: rgba(75,75,85,220); }"
            )

    def _on_pin(self):
        self._pinned = self.btn_pin.isChecked()
        self._refresh_pin_look()
        self.pin_toggled.emit(self._pinned)

    def _child_under(self, pos) -> bool:
        w = self.childAt(pos)
        return isinstance(w, QToolButton)

    def _begin_drag_notify(self):
        if not self._dragging:
            self._dragging = True
            self.drag_started.emit()

    def _end_drag_notify(self):
        if self._dragging:
            self._dragging = False
            self.drag_ended.emit()
        self._drag_pos = None
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

    def _poll_system_drag_end(self):
        """startSystemMove steals the mouse; poll until LMB released."""
        if QGuiApplication.mouseButtons() & Qt.LeftButton:
            return
        self._end_drag_notify()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._child_under(event.position().toPoint()):
            win = self.window()
            handle = win.windowHandle() if win else None
            if handle is not None:
                try:
                    self._begin_drag_notify()
                    handle.startSystemMove()
                    self._drag_pos = None
                    if self._poll_timer is None:
                        self._poll_timer = QTimer(self)
                        self._poll_timer.setInterval(16)
                        self._poll_timer.timeout.connect(self._poll_system_drag_end)
                    self._poll_timer.start()
                    event.accept()
                    return
                except Exception:
                    self._end_drag_notify()
            self._begin_drag_notify()
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            win = self.window()
            if win and not win.isMaximized():
                delta = event.globalPosition().toPoint() - self._drag_pos
                win.move(win.pos() + delta)
                self._drag_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._end_drag_notify()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if (
            self._show_max
            and event.button() == Qt.LeftButton
            and not self._child_under(event.position().toPoint())
        ):
            self.maximize_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
