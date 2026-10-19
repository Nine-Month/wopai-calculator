"""Automation progress panel and end-of-run summary dialog.

The summary is a frameless frosted card: light text, no window-chrome close
button. The only close control sits centered under the body.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.auto_plan import format_hms, progress_bar_text, progress_fraction
from .win_acrylic import try_enable_acrylic


class AutoTextBar(QWidget):
    """Progress bar with the campaign / keypoint text drawn inside the track."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setMaximumHeight(52)
        self._frac = 0.0
        self._line1 = "行动 0/3 · 据点 0/0"
        self._line2 = "运行 00:00:00"

    def set_progress(self, frac: float, line1: str, line2: str) -> None:
        self._frac = max(0.0, min(1.0, float(frac)))
        self._line1 = line1 or ""
        self._line2 = line2 or ""
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        if w < 4 or h < 4:
            p.end()
            return
        radius = 8.0
        track = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        p.setPen(QPen(QColor(255, 214, 120, 90), 1.0))
        p.setBrush(QColor(28, 26, 20, 230))
        p.drawRoundedRect(track, radius, radius)
        fill_w = max(0.0, (w - 1.0) * self._frac)
        if fill_w > 1:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(196, 148, 42, 210))
            p.setClipRect(QRectF(0.5, 0.5, fill_w, h - 1.0))
            p.drawRoundedRect(track, radius, radius)
            p.setClipping(False)
        p.setPen(QColor("#f7f1e4"))
        font = QFont(self.font())
        font.setPointSize(9)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.drawText(track.adjusted(6, 2, -6, -h / 2), int(Qt.AlignCenter), self._line1)
        font.setPointSize(8)
        font.setWeight(QFont.Normal)
        p.setFont(font)
        p.setPen(QColor("#ffe7b0"))
        p.drawText(track.adjusted(6, h / 2 - 2, -6, -2), int(Qt.AlignCenter), self._line2)
        p.end()


class AutoProgressPanel(QWidget):
    """Replaces the formation-card list while automation is running."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AutoProgress")
        self.setStyleSheet(
            "QWidget#AutoProgress { background-color: #2e2e34; }"
            "QLabel { color: #f2f2f6; background: transparent; }"
            "QLabel#AutoHint { color: #b7b7c2; font-size: 11px; }"
            "QLabel#AutoValue { color: #ffe7b0; font-weight: 700; font-size: 14px; }"
            "QLabel#AutoStep { color: #d5d5de; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        hint = QLabel("预测下一场")
        hint.setObjectName("AutoHint")
        lay.addWidget(hint)

        self.kp_name = QLabel("—")
        self.kp_name.setObjectName("AutoValue")
        self.kp_name.setWordWrap(True)
        lay.addWidget(self.kp_name)

        self.kp_power = QLabel("对方战力合计 —")
        self.kp_power.setWordWrap(True)
        lay.addWidget(self.kp_power)

        self.kp_reason = QLabel("")
        self.kp_reason.setObjectName("AutoHint")
        self.kp_reason.setWordWrap(True)
        lay.addWidget(self.kp_reason)

        self.bar = AutoTextBar()
        lay.addWidget(self.bar)

        self.tokens = QLabel("末阶段进度代币  全额 0 · 未获 0")
        self.tokens.setWordWrap(True)
        lay.addWidget(self.tokens)

        self.step = QLabel("等待开始")
        self.step.setObjectName("AutoStep")
        self.step.setWordWrap(True)
        lay.addWidget(self.step)
        lay.addStretch(1)

        self._elapsed = 0.0
        self._lobby: dict = {}

    def set_elapsed(self, seconds: float) -> None:
        self._elapsed = max(0.0, float(seconds))
        self._paint_bar()

    def update_info(self, info: dict) -> None:
        info = info or {}
        name = info.get("name") or "—"
        self.kp_name.setText(str(name))
        power = info.get("enemy_power")
        if power is None:
            self.kp_power.setText("对方战力合计 —")
        else:
            self.kp_power.setText(f"对方战力合计 {float(power):.0f}")
        self.kp_reason.setText(str(info.get("reason") or ""))
        full = int(info.get("token_full") or 0)
        missed = int(info.get("token_missed") or 0)
        self.tokens.setText(f"末阶段进度代币  全额 {full} · 未获 {missed}")
        step = str(info.get("step") or "")
        goal = info.get("stage_goal") or {}
        if goal:
            rem = goal.get("remaining")
            if rem is not None:
                step = (step + f" · 阶段还差 {int(rem)} 个摧毁").strip(" ·")
        self.step.setText(step or "进行中")
        if info.get("lobby"):
            self._lobby = dict(info["lobby"])
        self._paint_bar()

    def _paint_bar(self) -> None:
        lobby = self._lobby or {}
        line1 = progress_bar_text(lobby) if lobby else "行动 0/3 · 据点 0/0"
        line2 = f"运行 {format_hms(self._elapsed)}"
        frac = progress_fraction(lobby) if lobby else 0.0
        self.bar.set_progress(frac, line1, line2)


_SUMMARY_QSS = """
QDialog#AutoSummary { background: transparent; }
QFrame#AutoSummaryFrame {
    background-color: rgba(28, 28, 34, 214);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 12px;
}
QLabel { color: #f3f3f7; background: transparent; }
QLabel#SummaryTitle {
    color: #fff6df;
    font-size: 15px;
    font-weight: 700;
}
QLabel#SummaryHint { color: #c8c8d2; font-size: 12px; }
QPushButton#AutoSummaryClose {
    min-width: 128px;
    min-height: 32px;
    font-weight: 700;
    color: #f7f7fb;
    background-color: rgba(78, 78, 90, 210);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 6px;
}
QPushButton#AutoSummaryClose:hover {
    background-color: rgba(98, 98, 112, 220);
}
"""


class AutoSummaryDialog(QDialog):
    """Same card for a normal finish and a user stop. No title-bar close."""

    def __init__(self, summary: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("AutoSummary")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setStyleSheet(_SUMMARY_QSS)
        self.setMinimumWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("AutoSummaryFrame")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        title = QLabel("自动化窝牌汇总")
        title.setObjectName("SummaryTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        reason = str((summary or {}).get("reason") or "结束")
        elapsed = format_hms(float((summary or {}).get("elapsed_sec") or 0))
        battles = int((summary or {}).get("battles") or 0)
        head = QLabel(f"{reason} · 运行 {elapsed} · 战斗 {battles} 场")
        head.setAlignment(Qt.AlignCenter)
        head.setWordWrap(True)
        lay.addWidget(head)

        body = QLabel(self._body_text(summary or {}))
        body.setObjectName("SummaryHint")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.addWidget(body)
        scroll.setWidget(host)
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(320)
        lay.addWidget(scroll)

        close = QPushButton("关闭")
        close.setObjectName("AutoSummaryClose")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close, 0, Qt.AlignCenter)
        row.addStretch(1)
        lay.addLayout(row)

        outer.addWidget(frame)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        try:
            try_enable_acrylic(int(self.winId()), 210)
        except Exception:
            pass

    @staticmethod
    def _body_text(summary: dict) -> str:
        lines: list[str] = []
        stats = summary.get("operation_stats") or []
        if not stats:
            lines.append("各行动进度代币：暂无记录")
        else:
            lines.append("各行动进度代币（末阶段）")
            for st in stats:
                name = st.get("name") or st.get("id") or "行动"
                full = int(st.get("full") or 0)
                missed = int(st.get("missed") or 0)
                pf = st.get("points_full")
                pm = st.get("points_missed")
                extra = ""
                if pf or pm:
                    extra = f"（全额 { _fmt_num(pf) } 分 / 未获 { _fmt_num(pm) } 分）"
                cleared = st.get("cleared")
                total = st.get("total")
                kp = ""
                if cleared is not None and total is not None:
                    kp = f" · 据点 {int(cleared)}/{int(total)}"
                lines.append(f"· {name}{kp}：全额 {full} · 未获 {missed}{extra}")
        res = summary.get("resource_line") or ""
        lines.append("")
        lines.append("本次记到的奖励：" + (res or "无"))
        notes = [str(n) for n in (summary.get("notes") or []) if n]
        if notes:
            lines.append("")
            lines.append("说明")
            for n in notes[:8]:
                lines.append("· " + n)
        return "\n".join(lines)


def _fmt_num(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(v - int(v)) < 1e-6:
        return str(int(v))
    return f"{v:g}"
