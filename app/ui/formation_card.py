"""One selectable formation result card (expand / collapse)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..mod_names import mods_label
from ..worker import MODE_LABELS
from .formation_hover import shared_hover_tip


def win_rate_color(pct) -> str:
    """100% green → low red gradient (for collapsed summary / tags)."""
    try:
        t = max(0.0, min(1.0, float(pct) / 100.0))
    except Exception:
        t = 0.5
    # red #e07070 → green #5dce8a
    r1, g1, b1 = 224, 112, 112
    r2, g2, b2 = 93, 206, 138
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):.0f}%"
    except Exception:
        return "—"


def _total_overflow(sol: dict) -> float:
    """Prefer result.total_overflow; else sum per-lane overflow (estimated)."""
    if sol.get("total_overflow") is not None:
        try:
            return float(sol.get("total_overflow") or 0)
        except Exception:
            pass
    # Fallback: sum lane overflow fields when top-level total is missing
    total = 0.0
    for slot in sol.get("slots") or []:
        if slot.get("status") == "empty":
            continue
        try:
            total += float(slot.get("overflow") or 0)
        except Exception:
            continue
    return total


class FormationCard(QFrame):
    clicked = Signal(dict)
    fold_changed = Signal(bool)  # collapsed

    def __init__(self, sol: dict, rank: int, parent=None, *, collapsed: bool = False):
        super().__init__(parent)
        self.setObjectName("FormationCard")
        self.sol = sol
        self.rank = rank
        self._collapsed = bool(collapsed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.NoFrame)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(8, 6, 8, 6)
        self._root.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(4)
        mode = sol.get("_mode") or ""
        mode_zh = MODE_LABELS.get(mode, mode)
        avg = sol.get("win_rate_avg")
        mn = sol.get("win_rate_min")
        if avg is None:
            certain = bool(sol.get("all_certain"))
            tag_txt = "全确" if certain else "部分"
            tag_obj = "TagCertain" if certain else "TagBand"
        else:
            tag_txt = f"综合 {_fmt_pct(avg)}"
            tag_obj = "TagCertain" if float(mn or 0) >= 99.5 else "TagBand"

        # Fixed-width rank column (digits only, no "#") so win-rate text
        # starts at the same X regardless of 1 vs 10 vs 100.
        self._rank_l = QLabel(str(int(rank)))
        self._rank_l.setFixedWidth(28)
        self._rank_l.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._rank_l.setStyleSheet("color:#e8e8ea;background:transparent;")
        self._mode_l = QLabel(mode_zh)
        self._mode_l.setStyleSheet("color:#c8c8d0;background:transparent;")
        self._tag = QLabel(tag_txt)
        self._tag.setObjectName(tag_obj)
        self._tag.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        tot = _total_overflow(sol)
        # Win-rate color on tag / summary win-rate text (100 green → low red)
        color_src = avg if avg is not None else (100.0 if sol.get("all_certain") else 50.0)
        try:
            color_src = float(mn if mn is not None and avg is not None else color_src)
        except Exception:
            color_src = 50.0
        try:
            wr = float(avg) if avg is not None else float(color_src)
        except Exception:
            wr = 50.0
        wr_color = win_rate_color(wr)
        # Collapsed summary only: overflow ≥100 → green; otherwise red (no mid gradient)
        ov_color = win_rate_color(100.0 if float(tot) >= 100.0 else 0.0)
        if avg is None:
            summary = (
                f'<span style="color:{wr_color}">{tag_txt}</span>'
                f' · <span style="color:{ov_color}">溢出 {tot:+.0f}</span>'
            )
        else:
            summary = (
                f'<span style="color:{wr_color}">综合 {_fmt_pct(avg)}</span>'
                f' · <span style="color:{ov_color}">溢出 {tot:+.0f}</span>'
            )
        self._summary = QLabel(summary)
        self._summary.setObjectName(tag_obj)
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._tag.setStyleSheet(
            f"color:{wr_color};font-weight:700;"
            f"background-color:rgba(0,0,0,0);border:none;padding:1px 2px;"
        )
        # No solid color here — rich-text spans carry win-rate / overflow colors
        self._summary.setStyleSheet(
            "font-weight:700;background-color:rgba(0,0,0,0);border:none;"
        )

        self.btn_fold = QToolButton()
        self.btn_fold.setObjectName("CardFoldBtn")
        self.btn_fold.setAutoRaise(True)
        self.btn_fold.setCursor(Qt.ArrowCursor)
        self.btn_fold.setFixedSize(22, 22)
        self.btn_fold.clicked.connect(self._toggle_fold)

        # Rank fixed; win-rate / summary left-aligned in the remaining stretch.
        head.addWidget(self._rank_l, 0, Qt.AlignLeft | Qt.AlignVCenter)
        head.addWidget(self._mode_l, 0, Qt.AlignLeft | Qt.AlignVCenter)
        head.addWidget(self._tag, 0, Qt.AlignLeft | Qt.AlignVCenter)
        head.addWidget(self._summary, 1, Qt.AlignLeft | Qt.AlignVCenter)
        head.addWidget(self.btn_fold, 0, Qt.AlignTop | Qt.AlignRight)
        self._root.addLayout(head)

        self._body = QWidget()
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(3)

        for slot in sol.get("slots") or []:
            if slot.get("status") == "empty":
                empty_l = QLabel("  空位")
                empty_l.setStyleSheet("color:#a0a0aa;background:transparent;")
                body_lay.addWidget(empty_l)
                continue
            line = QWidget()
            hl = QHBoxLayout(line)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)
            ship = slot.get("ship") or "?"
            mods = slot.get("mods_zh") or mods_label(slot.get("mods"))
            name = ship if not mods else f"{ship}+{mods}"
            band = slot.get("power_band") or [0, 0]
            enemy = slot.get("enemy") or "?"
            ep = slot.get("enemy_power", 0)
            ov = float(slot.get("overflow") or 0)
            left = QLabel(f"{name}")
            left.setWordWrap(True)
            left.setStyleSheet("color:#e8e8ea;background:transparent;")
            mid = QLabel(f"{band[0]:.0f}–{band[1]:.0f} vs {enemy} {ep:.0f}")
            mid.setStyleSheet("color:#b0b0ba;background:transparent;")
            ov_l = QLabel(f"{ov:+.0f}")
            ov_l.setObjectName("OverflowPos" if ov >= 0 else "OverflowNeg")
            hl.addWidget(left, 2)
            hl.addWidget(mid, 2)
            hl.addWidget(ov_l, 0, Qt.AlignRight)
            body_lay.addWidget(line)

        foot = QHBoxLayout()
        tot_l = QLabel(f"总溢出 {tot:+.1f}")
        tot_l.setObjectName("OverflowPos" if tot >= 0 else "OverflowNeg")
        foot.addWidget(tot_l)
        foot.addStretch(1)
        tip = QLabel("点击放牌")
        tip.setStyleSheet("color:#c0c0c8;background:transparent;")
        foot.addWidget(tip)
        body_lay.addLayout(foot)

        self._root.addWidget(self._body)
        self._apply_fold_ui()
        self._make_children_mouse_transparent()

    def _apply_fold_ui(self):
        collapsed = self._collapsed
        self._body.setVisible(not collapsed)
        self._tag.setVisible(not collapsed)
        self._mode_l.setVisible(not collapsed)
        self._summary.setVisible(collapsed)
        self.btn_fold.setText("▸" if collapsed else "▾")
        self.btn_fold.setToolTip("展开" if collapsed else "折叠")

    def set_collapsed(self, collapsed: bool, *, notify: bool = True):
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._apply_fold_ui()
        if notify:
            self.fold_changed.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _toggle_fold(self):
        self.set_collapsed(not self._collapsed)

    def _make_children_mouse_transparent(self):
        for w in self.findChildren(QWidget):
            if w is self or w is self.btn_fold:
                continue
            # Keep fold button clickable; everything else passes clicks to card
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.btn_fold.setAttribute(Qt.WA_TransparentForMouseEvents, False)


    def enterEvent(self, event):
        tip = shared_hover_tip()
        tip.populate(self.sol)
        tip.popup_at(self.mapToGlobal(self.rect().topRight()))
        super().enterEvent(event)

    def leaveEvent(self, event):
        tip = shared_hover_tip()
        tip.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Ignore clicks on the fold button (it handles itself)
            child = self.childAt(event.position().toPoint())
            if child is self.btn_fold or (
                child is not None and self.btn_fold.isAncestorOf(child)
            ):
                super().mouseReleaseEvent(event)
                return
            self.clicked.emit(self.sol)
        super().mouseReleaseEvent(event)
