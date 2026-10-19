"""Flat dark-gray frosted look."""

APP_QSS = """
QToolTip {
    background-color: rgba(36, 36, 42, 235);
    color: #e8e8ea;
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 500;
}

QWidget#Root {
    background-color: #1c1c1f;
    color: #e8e8ea;
    font-family: "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif;
    font-size: 12px;
}
QWidget#TitleBar {
    background-color: rgba(43, 43, 48, 220);
    border-bottom: 1px solid rgba(255,255,255,0.06);
}
QLabel#TitleLabel {
    color: #f0f0f2;
    font-weight: 600;
    font-size: 12px;
    padding-left: 6px;
}
QToolButton, QPushButton {
    background-color: rgba(55, 55, 62, 200);
    color: #e8e8ea;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 4px;
    padding: 4px 8px;
}
QToolButton {
    padding: 0px;
    margin: 0px;
}
QToolButton:hover, QPushButton:hover {
    /* Color snap removed — soft_hover.py fades a light veil instead */
    background-color: rgba(55, 55, 62, 200);
}
QToolButton:pressed, QPushButton:pressed {
    background-color: rgba(40, 40, 48, 220);
}
QToolButton:checked {
    background-color: rgba(70, 110, 180, 200);
}
QToolButton#CloseBtn:hover {
    /* soft_hover veil turns this red gently */
    background-color: rgba(55, 55, 62, 200);
    color: #fff;
}
QToolButton#CardFoldBtn {
    background-color: rgba(55, 55, 62, 180);
    color: #c8d4e8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 3px;
    font-size: 11px;
    padding: 0;
}
QToolButton#CardFoldBtn:hover {
    background-color: rgba(55, 55, 62, 180);
    color: #fff;
}
QPushButton:disabled {
    color: #777;
    background-color: rgba(40,40,45,160);
}
QPushButton#ModeBtn {
    font-weight: 600;
    font-size: 12px;
}
QPushButton#AutoBtn {
    font-weight: 700;
    font-size: 12px;
    background-color: rgba(175, 135, 35, 225);
    color: #fff6d8;
    border: 1px solid rgba(230, 190, 70, 0.55);
}
QPushButton#AutoBtn:hover {
    background-color: rgba(190, 150, 45, 235);
    border: 1px solid rgba(240, 205, 90, 0.65);
    color: #fffcef;
}
QPushButton#AutoBtn:pressed {
    background-color: rgba(145, 110, 25, 240);
}
QPushButton#AutoBtn:disabled {
    background-color: rgba(70, 60, 35, 160);
    color: #998;
    border: 1px solid rgba(120, 100, 50, 0.3);
}
QPushButton#ModFilterBtn {
    min-width: 64px;
    max-width: 72px;
    font-weight: 600;
    font-size: 12px;
    padding: 1px 6px;
    /* Distinct from ModeBtn: outline / muted teal — not a "start solve" look */
    background-color: rgba(40, 55, 58, 200);
    color: #c5e0dc;
    border: 1px solid rgba(140, 190, 180, 0.45);
}
QPushButton#ModFilterBtn:hover {
    background-color: rgba(40, 55, 58, 200);
    border: 1px solid rgba(140, 190, 180, 0.45);
    color: #e8f6f3;
}
QPushButton#ModFilterBtn:pressed {
    background-color: rgba(30, 42, 44, 220);
}
QPushButton#StopBtn {
    min-width: 64px;
    max-width: 72px;
    font-weight: 700;
    font-size: 11px;
    padding: 1px 4px;
    background-color: rgba(140, 42, 42, 230);
    color: #ffe8e8;
    border: 1px solid rgba(220, 110, 110, 0.55);
}
QPushButton#StopBtn:hover {
    background-color: rgba(140, 42, 42, 230);
    border: 1px solid rgba(220, 110, 110, 0.55);
    color: #fff;
}
QPushButton#StopBtn:pressed {
    background-color: rgba(110, 30, 30, 240);
}
QPushButton#StopBtn:disabled {
    background-color: rgba(60, 40, 40, 160);
    color: #988;
    border: 1px solid rgba(120, 80, 80, 0.3);
}
QPushButton#PrimaryBtn {
    min-height: 34px;
    max-height: 34px;
    font-weight: 700;
    font-size: 13px;
    background-color: rgba(55, 105, 190, 230);
    border: 1px solid rgba(120,170,255,0.45);
    color: #f2f6ff;
}
QPushButton#PrimaryBtn:hover {
    background-color: rgba(55, 105, 190, 230);
}
QPushButton#PrimaryBtn:disabled {
    background-color: rgba(40,40,50,180);
    color: #888;
}
QPushButton#SecondaryBtn {
    min-height: 34px;
    max-height: 34px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#ConnBadge {
    color: #e07070;
    font-weight: 700;
    font-size: 12px;
    padding: 4px 8px;
    background-color: rgba(40, 28, 28, 180);
    border-bottom: 1px solid rgba(255,255,255,0.05);
}
QLabel#ConnBadge[connected="true"] {
    color: #5dce8a;
    background-color: rgba(28, 40, 32, 180);
}
QScrollArea {
    background-color: #2e2e34;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: #2e2e34;
}
QWidget#ListHost {
    background-color: #2e2e34;
}
QScrollBar:vertical {
    background: rgba(20,20,24,120);
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(120,120,130,160);
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QFrame#FormationCard {
    background-color: rgba(38, 38, 44, 210);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
}
QFrame#FormationCard:hover {
    border: 1px solid rgba(120,160,220,0.45);
    background-color: rgba(48, 48, 56, 230);
}
QFrame#FormationCard QLabel {
    color: #e4e4ea;
    background: transparent;
}
QLabel#OverflowPos { color: #e07070; font-weight: 600; }  /* ≥0 红 */
QLabel#OverflowNeg { color: #5dce8a; font-weight: 600; }  /* <0 绿 */
QLabel#StatusLine {
    color: #a8a8b0;
    padding: 2px 8px;
    min-height: 40px;
    max-height: 52px;
    font-size: 12px;
}
QLabel#TagCertain {
    background-color: rgba(50,140,90,180);
    color: #dff8e8;
    border-radius: 3px;
    padding: 1px 5px;
}
QLabel#TagBand {
    background-color: rgba(140,110,40,180);
    color: #fff3d0;
    border-radius: 3px;
    padding: 1px 5px;
}
QDialog {
    background-color: #2b2b30;
    color: #e8e8ea;
}
QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #1c1c1f;
    color: #e8e8ea;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 3px 6px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #444;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px;
    margin: -5px 0;
    background: #8ab4f8;
    border-radius: 6px;
}

/* Hard-striped blue solve progress — Bootstrap/NudaUI style (animated in main_window) */
QProgressBar#SolveProgress {
    background-color: rgba(22, 32, 48, 235);
    border: 1px solid rgba(130, 185, 255, 0.35);
    border-radius: 7px;
    text-align: center;
    color: #eef5ff;
    font-size: 11px;
    font-weight: 600;
    min-height: 22px;
    max-height: 24px;
    padding: 0px;
    margin: 0px;
}
QProgressBar#SolveProgress::chunk {
    /* Hard cuts at 25/50/75 — same idea as Bootstrap gradient-striped */
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0.00 #1e6bb5, stop:0.125 #1e6bb5,
        stop:0.125 #8ec8ff, stop:0.25 #8ec8ff,
        stop:0.25 #1e6bb5, stop:0.375 #1e6bb5,
        stop:0.375 #8ec8ff, stop:0.50 #8ec8ff,
        stop:0.50 #1e6bb5, stop:0.625 #1e6bb5,
        stop:0.625 #8ec8ff, stop:0.75 #8ec8ff,
        stop:0.75 #1e6bb5, stop:0.875 #1e6bb5,
        stop:0.875 #8ec8ff, stop:1.00 #8ec8ff);
    border-radius: 6px;
    margin: 0px;
}
"""

# Dedicated stylesheet for settings dialog — force light labels on dark bg
SETTINGS_QSS = """
QDialog {
    background-color: #2b2b30;
    color: #e8e8ea;
    font-family: "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif;
    font-size: 12px;
}
QLabel, QLabel#SettingsLabel {
    color: #e0e0e6;
    background: transparent;
}
QLabel#SettingsHint {
    color: #9a9aa4;
    background: transparent;
    font-size: 11px;
}
QSpinBox, QDoubleSpinBox, QAbstractSpinBox {
    background-color: #1c1c1f;
    color: #e8e8ea;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 3px 6px;
    min-height: 22px;
    selection-color: #ffffff;
    selection-background-color: #4a6fa5;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #44444c;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #6a9ae8;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: #c8d8f8;
    border: 1px solid #8ab4f8;
    border-radius: 7px;
}
QFormLayout {
    color: #e8e8ea;
}
QCheckBox {
    color: #f0f0f4;
    background: transparent;
    font-size: 13px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #7a7a88;
    border-radius: 3px;
    background: #1c1c1f;
}
QCheckBox::indicator:checked {
    background: #5a8fd4;
    border: 1px solid #8ab4f8;
}
QCheckBox::indicator:hover {
    border: 1px solid #a0b8e0;
}
"""
