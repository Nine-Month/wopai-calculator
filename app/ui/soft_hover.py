"""Soft hover fade for buttons (Qt QSS has no CSS transitions)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, QPropertyAnimation, QEasingCurve, Qt
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QWidget

_MS = 320
_SKIP_TYPES = {"_ModTile"}


def _veil_style(object_name: str) -> tuple[str, float]:
    """Return (stylesheet, peak_opacity) for the brightening veil."""
    name = object_name or ""
    if name == "CloseBtn":
        return (
            "background-color: rgb(200, 55, 55); border-radius: 4px;",
            0.85,
        )
    if name == "StopBtn":
        return (
            "background-color: rgb(255, 170, 170); border-radius: 4px;",
            0.22,
        )
    if name == "PrimaryBtn":
        return (
            "background-color: rgb(180, 210, 255); border-radius: 4px;",
            0.18,
        )
    if name == "ModFilterBtn":
        return (
            "background-color: rgb(180, 230, 220); border-radius: 4px;",
            0.16,
        )
    if name == "CardFoldBtn":
        return (
            "background-color: rgb(160, 180, 220); border-radius: 3px;",
            0.20,
        )
    return (
        "background-color: rgb(255, 255, 255); border-radius: 4px;",
        0.12,
    )


class _SoftHover(QObject):
    def __init__(self, btn: QAbstractButton):
        super().__init__(btn)
        self._btn = btn
        ss, peek = _veil_style(btn.objectName())
        self._peek = peek
        self._veil = QWidget(btn)
        self._veil.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._veil.setStyleSheet(ss)
        self._veil.hide()
        self._fx = QGraphicsOpacityEffect(self._veil)
        self._fx.setOpacity(0.0)
        self._veil.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(_MS)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        btn.installEventFilter(self)
        btn.setProperty("_softHover", True)
        self._sync_geo()

    def _sync_geo(self):
        self._veil.setGeometry(self._btn.rect())
        self._veil.raise_()

    def _fade_to(self, end: float):
        self._anim.stop()
        if end > 0.0:
            self._veil.show()
            self._veil.raise_()
        self._anim.setStartValue(float(self._fx.opacity()))
        self._anim.setEndValue(float(end))
        self._anim.start()

    def _on_anim_finished(self):
        if self._fx.opacity() <= 0.001:
            self._veil.hide()

    def eventFilter(self, obj, ev):  # noqa: N802
        t = ev.type()
        if t == QEvent.Type.Resize:
            self._sync_geo()
        elif t == QEvent.Type.Enter:
            if self._btn.isEnabled():
                self._fade_to(self._peek)
        elif t == QEvent.Type.Leave:
            self._fade_to(0.0)
        elif t == QEvent.Type.EnabledChange:
            if not self._btn.isEnabled():
                self._fade_to(0.0)
        return False


def install_soft_hover(root: QWidget) -> None:
    """Attach soft hover fade to every QAbstractButton under root (idempotent)."""
    if root is None:
        return
    for btn in root.findChildren(QAbstractButton):
        if btn.property("_softHover"):
            continue
        if type(btn).__name__ in _SKIP_TYPES:
            continue
        _SoftHover(btn)
