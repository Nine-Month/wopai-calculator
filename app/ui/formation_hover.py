"""Formation result hover tip: per-lane power line + mod icons (or empty highlight box)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..mod_catalog import ensure_mod_icon

_ICON = 28
_PAD = 2
_TILE = _ICON + _PAD * 2


def _mod_icon_widget(mid: str, parent=None) -> QLabel:
    lab = QLabel(parent)
    lab.setFixedSize(_TILE, _TILE)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet(
        "QLabel {"
        "  background-color: rgba(40,40,48,220);"
        "  border: 2px solid rgba(255,255,255,200);"
        "  border-radius: 5px;"
        "  padding: 1px;"
        "}"
    )
    path = ensure_mod_icon(mid)
    if path is not None:
        pm = QPixmap(str(path))
        if not pm.isNull():
            lab.setPixmap(pm.scaled(_ICON, _ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            return lab
    lab.setText((mid or "?")[:1])
    lab.setStyleSheet(
        lab.styleSheet()
        + "QLabel { color:#e8e8ea; font-size:11px; font-weight:600; }"
    )
    return lab


def _empty_mod_box(parent=None) -> QLabel:
    """One highlighted empty frame when the lane has no modifiers."""
    lab = QLabel(parent)
    lab.setFixedSize(_TILE, _TILE)
    lab.setStyleSheet(
        "QLabel {"
        "  background-color: rgba(40,40,48,120);"
        "  border: 2px solid rgba(255,255,255,200);"
        "  border-radius: 5px;"
        "}"
    )
    return lab


class FormationHoverTip(QFrame):
    """Frameless popup matching app glass tip style."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("FormationHoverTip")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "QFrame#FormationHoverTip {"
            "  background-color: rgba(36, 36, 42, 242);"
            "  color: #e8e8ea;"
            "  border: 1px solid rgba(255,255,255,0.14);"
            "  border-radius: 6px;"
            "}"
            "QLabel { background: transparent; color: #e8e8ea; font-size: 12px; }"
        )
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(10, 8, 10, 8)
        self._lay.setSpacing(6)
        self.hide()

    def populate(self, sol: dict) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    it = sub.takeAt(0)
                    if it.widget():
                        it.widget().deleteLater()

        slots = list(sol.get("slots") or [])
        # Always show 4 lanes in order
        by_idx = {}
        for s in slots:
            try:
                by_idx[int(s.get("slot", len(by_idx)))] = s
            except Exception:
                by_idx[len(by_idx)] = s

        for i in range(4):
            slot = by_idx.get(i) or (slots[i] if i < len(slots) else None)
            block = QVBoxLayout()
            block.setSpacing(3)
            block.setContentsMargins(0, 0, 0, 0)

            if not slot or slot.get("status") == "empty":
                line = QLabel(f"{i + 1}  （空）")
                line.setStyleSheet("color:#9a9aa4;")
                block.addWidget(line)
                icons = QHBoxLayout()
                icons.setSpacing(4)
                icons.setContentsMargins(18, 0, 0, 0)
                icons.addWidget(_empty_mod_box())
                icons.addStretch(1)
                block.addLayout(icons)
                self._lay.addLayout(block)
                continue

            ship = str(slot.get("ship") or "?")
            # Prefer short-looking name as stored; do not append mod names
            band = slot.get("power_band") or [0, 0]
            try:
                lo, hi = float(band[0]), float(band[1])
            except Exception:
                lo = hi = 0.0
            enemy = str(slot.get("enemy") or "?")
            try:
                ep = float(slot.get("enemy_power") or 0)
            except Exception:
                ep = 0.0
            try:
                ov = float(slot.get("overflow") or 0)
            except Exception:
                ov = 0.0
            ov_s = f"{ov:+.0f}"
            row = QHBoxLayout()
            row.setSpacing(6)
            row.setContentsMargins(0, 0, 0, 0)
            n = QLabel(str(i + 1))
            n.setFixedWidth(14)
            n.setStyleSheet("color:#c8c8d0;font-weight:600;")
            name = QLabel(ship)
            name.setMinimumWidth(72)
            name.setStyleSheet("color:#e8e8ea;font-weight:600;")
            mid = QLabel(f"{lo:.0f}–{hi:.0f} | {enemy}  {ep:.0f} | {ov_s}")
            mid.setStyleSheet("color:#c8c8d0;")
            row.addWidget(n, 0)
            row.addWidget(name, 0)
            row.addWidget(mid, 1)
            block.addLayout(row)

            mods = list(slot.get("mods") or [])
            icons = QHBoxLayout()
            icons.setSpacing(4)
            icons.setContentsMargins(18, 0, 0, 0)
            if mods:
                for mid in mods:
                    icons.addWidget(_mod_icon_widget(str(mid)))
            else:
                icons.addWidget(_empty_mod_box())
            icons.addStretch(1)
            block.addLayout(icons)
            self._lay.addLayout(block)

        self.adjustSize()

    def popup_at(self, global_pos: QPoint) -> None:
        self.move(global_pos + QPoint(14, 12))
        self.show()
        self.raise_()


_shared_tip: FormationHoverTip | None = None


def shared_hover_tip() -> FormationHoverTip:
    global _shared_tip
    if _shared_tip is None:
        _shared_tip = FormationHoverTip()
    return _shared_tip
