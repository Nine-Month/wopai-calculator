"""Combat modifier filter: frameless, game-like left/right layout."""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..mod_catalog import (
    COMBAT_MOD_IDS,
    PURCHASABLE_COMBAT,
    UNPURCHASABLE_COMBAT,
    ensure_mod_icon,
    mod_label,
)
from ..mod_names import mod_desc
from .styles import APP_QSS, SETTINGS_QSS
from .soft_hover import install_soft_hover
from .title_bar import TitleBar
from .win_acrylic import try_enable_acrylic
from ..settings import load_settings

# Soft off-white (matches panel edges)
_SEL_BORDER = (255, 255, 255, 220)
_UNSEL_BORDER = (255, 255, 255, 0)
_SEL_BG = (52, 52, 58, 230)
_UNSEL_BG = (40, 40, 46, 120)
_HOVER_BOOST = 0.08
_OP_ON = 1.0
_OP_OFF = 0.38
_ANIM_MS = 320  # soft fade; longer + ease in-out


def _rgba(t) -> str:
    r, g, b, a = t
    return f"rgba({r},{g},{b},{a})"


class _ModTile(QToolButton):
    """Icon tile with soft opacity / border fade on select and hover."""

    def __init__(self, mid: str, parent=None):
        super().__init__(parent)
        self.mid = mid
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 58)
        self.setToolTip(mod_desc(mid))
        self.setObjectName("ModTile")
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(_OP_OFF)
        self._border_a = 0.0
        self._hover = False
        self._op_anim: QPropertyAnimation | None = None
        self._bd_anim: QPropertyAnimation | None = None

        path = ensure_mod_icon(mid)
        if path is not None:
            pm = QPixmap(str(path))
            if not pm.isNull():
                scaled = pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.setIcon(scaled)
                self.setIconSize(scaled.size())
            else:
                self.setText(mod_label(mid)[:2])
        else:
            self.setText(mod_label(mid)[:2])

        self.toggled.connect(self._on_toggled)
        self._apply_style(immediate=True)

    def _get_border_a(self) -> float:
        return float(self._border_a)

    def _set_border_a(self, v: float):
        self._border_a = float(v)
        self._paint_style()

    borderAlpha = Property(float, _get_border_a, _set_border_a)

    def enterEvent(self, event):
        self._hover = True
        self._animate_to(self.isChecked(), hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._animate_to(self.isChecked(), hover=False)
        super().leaveEvent(event)

    def _on_toggled(self, checked: bool):
        self._animate_to(checked, hover=self._hover)

    def _target_opacity(self, checked: bool, hover: bool) -> float:
        base = _OP_ON if checked else _OP_OFF
        if hover:
            # Soft lift; never exceed 1.0, never jump below base by much
            return min(1.0, base + _HOVER_BOOST)
        return base

    def _target_border(self, checked: bool, hover: bool) -> float:
        if checked:
            return 1.0
        if hover:
            return 0.28
        return 0.0

    def _animate_to(self, checked: bool, hover: bool):
        op_to = self._target_opacity(checked, hover)
        bd_to = self._target_border(checked, hover)

        if self._op_anim is not None:
            self._op_anim.stop()
        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(_ANIM_MS)
        anim.setStartValue(float(self._opacity.opacity()))
        anim.setEndValue(op_to)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.start()
        self._op_anim = anim

        if self._bd_anim is not None:
            self._bd_anim.stop()
        banim = QPropertyAnimation(self, b"borderAlpha", self)
        banim.setDuration(_ANIM_MS)
        banim.setStartValue(float(self._border_a))
        banim.setEndValue(bd_to)
        banim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        banim.start()
        self._bd_anim = banim

    def _apply_style(self, immediate: bool = False):
        checked = self.isChecked()
        hover = self._hover
        if immediate:
            self._opacity.setOpacity(self._target_opacity(checked, hover))
            self._border_a = self._target_border(checked, hover)
        self._paint_style()

    def _paint_style(self):
        checked = self.isChecked()
        a = max(0.0, min(1.0, self._border_a))
        border = f"rgba(255,255,255,{int(round(220 * a))})"
        bg = _rgba(_SEL_BG) if checked else _rgba(_UNSEL_BG)
        self.setStyleSheet(
            f"QToolButton#ModTile {{"
            f"  background-color: {bg};"
            f"  border: 2px solid {border};"
            f"  border-radius: 6px;"
            f"  padding: 2px;"
            f"}}"
        )


def _section_title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet("color:#c8c8d0;font-size:12px;font-weight:600;padding:2px 0;")
    return lab


def _quick_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("ModeBtn")
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(22)
    b.setStyleSheet(
        "QPushButton#ModeBtn {"
        "  font-size:10px; padding:1px 6px; min-width:44px;"
        "  background-color: rgba(48,48,54,200);"
        "  border: 1px solid rgba(255,255,255,0.12);"
        "  border-radius: 4px; color:#d0d0d8;"
        "}"
        "QPushButton#ModeBtn:hover {"
        "  background-color: rgba(48,48,54,200);"
        "  border: 1px solid rgba(255,255,255,0.12);"
        "}"
    )
    return b


class ModFilterDialog(QDialog):
    """Frameless mod picker. Live-saves on toggle. Esc / × closes."""

    changed = Signal(list)  # list[str] enabled ids

    def __init__(self, enabled: list[str] | None, parent=None):
        super().__init__(parent)
        self.setObjectName("ModFilterDialog")
        self.setWindowTitle("道具选择")
        self.setModal(False)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumWidth(420)
        self.setMinimumHeight(320)
        enabled_set = set(enabled if enabled is not None else COMBAT_MOD_IDS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.panel = QFrame()
        self.panel.setObjectName("Root")
        self.panel.setStyleSheet(APP_QSS + SETTINGS_QSS)
        outer.addWidget(self.panel)

        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.title_bar = TitleBar(
            show_min=False, show_max=False, show_pin=False, show_settings=False
        )
        self.title_bar.set_title_text("道具选择")
        self.title_bar.close_clicked.connect(self.close)
        self.title_bar.drag_started.connect(lambda: None)
        lay.addWidget(self.title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 14)
        body.setSpacing(10)

        cols = QHBoxLayout()
        cols.setSpacing(0)
        cols.setContentsMargins(0, 0, 0, 0)

        self._tiles: list[_ModTile] = []
        self._tile_by_id: dict[str, _ModTile] = {}

        left = self._build_column(
            "可购买", PURCHASABLE_COMBAT, enabled_set, buy=True
        )
        right = self._build_column(
            "不可购买", UNPURCHASABLE_COMBAT, enabled_set, buy=False
        )

        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet(
            "QFrame { background-color: rgba(255,255,255,0.55); "
            "margin: 4px 12px; border: none; }"
        )
        divider.setMinimumHeight(160)

        cols.addLayout(left, 1)
        cols.addWidget(divider, 0)
        cols.addLayout(right, 1)
        body.addLayout(cols)

        lay.addLayout(body)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.reject)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self._acrylic_done = False
        self._glass_opacity = float(load_settings().opacity)
        self.setStyleSheet("QDialog#ModFilterDialog { background: transparent; }")
        self.panel.setStyleSheet(
            APP_QSS
            + SETTINGS_QSS
            + "QWidget#Root, QFrame#Root {"
            "  background-color: rgba(28, 28, 31, 200);"
            "}"
        )
        install_soft_hover(self)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self.setWindowOpacity(float(self._glass_opacity))
        except Exception:
            pass
        self._acrylic_done = False
        QTimer.singleShot(0, self._try_acrylic)

    def _try_acrylic(self):
        if self._acrylic_done:
            return
        try:
            hwnd = int(self.winId())
            try_enable_acrylic(hwnd, int(float(self._glass_opacity) * 255))
            self._acrylic_done = True
        except Exception:
            pass

    def _build_column(self, title: str, ids: list[str], enabled_set: set, *, buy: bool):
        col = QVBoxLayout()
        col.setSpacing(8)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(_section_title(title))

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        ncols = 2 if buy else 3
        for i, mid in enumerate(ids):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            cell.setContentsMargins(0, 0, 0, 0)
            tile = _ModTile(mid)
            tile.setChecked(mid in enabled_set)
            tile._apply_style(immediate=True)
            tile.toggled.connect(lambda *_: self._emit_changed())
            self._tiles.append(tile)
            self._tile_by_id[mid] = tile
            lab = QLabel(mod_label(mid))
            lab.setAlignment(Qt.AlignHCenter)
            lab.setWordWrap(True)
            lab.setFixedWidth(72)
            lab.setStyleSheet("color:#b0b0b8;font-size:10px;")
            cell.addWidget(tile, 0, Qt.AlignHCenter)
            cell.addWidget(lab)
            wrap = QWidget()
            wrap.setLayout(cell)
            grid.addWidget(wrap, i // ncols, i % ncols, Qt.AlignLeft | Qt.AlignTop)
        col.addWidget(grid_host, 1)

        # Quick buttons under this column, left-aligned
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 4, 0, 0)
        btn_on = _quick_btn("全选")
        btn_off = _quick_btn("取消全选")
        if buy:
            btn_on.setToolTip("全选可购买道具")
            btn_off.setToolTip("取消全部可购买道具")
            btn_on.clicked.connect(lambda: self._set_group(PURCHASABLE_COMBAT, True))
            btn_off.clicked.connect(lambda: self._set_group(PURCHASABLE_COMBAT, False))
        else:
            btn_on.setToolTip("全选不可购买道具")
            btn_off.setToolTip("取消全部不可购买道具")
            btn_on.clicked.connect(lambda: self._set_group(UNPURCHASABLE_COMBAT, True))
            btn_off.clicked.connect(lambda: self._set_group(UNPURCHASABLE_COMBAT, False))
        row.addWidget(btn_on, 0, Qt.AlignLeft)
        row.addWidget(btn_off, 0, Qt.AlignLeft)
        row.addStretch(1)
        col.addLayout(row)
        return col

    def _set_group(self, ids, checked: bool):
        want = set(ids)
        for tile in self._tiles:
            if tile.mid in want:
                tile.blockSignals(True)
                tile.setChecked(bool(checked))
                tile.blockSignals(False)
                tile._animate_to(tile.isChecked(), hover=tile._hover)
        self._emit_changed()

    def enabled_ids(self) -> list[str]:
        # Stable order as COMBAT_MOD_IDS
        on = {t.mid for t in self._tiles if t.isChecked()}
        return [m for m in COMBAT_MOD_IDS if m in on]

    def _emit_changed(self):
        self.changed.emit(self.enabled_ids())

    def reject(self):
        super().reject()

    def closeEvent(self, event):
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
