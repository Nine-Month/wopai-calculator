"""Settings panel: frameless dark glass, Esc/X close, live-save, open logs."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QFrame,
)

from ..logutil import get_logger, open_logs_folder
from ..settings import AppSettings, save_settings
from .styles import SETTINGS_QSS, APP_QSS
from .soft_hover import install_soft_hover
from .title_bar import TitleBar
from .win_acrylic import try_enable_acrylic

log = get_logger("settings")

_VALUE_LABEL_SS = (
    "color:#f0f0f2; background:#1a1a1e; border:1px solid #555; "
    "border-radius:4px; padding:4px 8px; font-size:14px; font-weight:600;"
)
_LINE_EDIT_SS = (
    "color:#f0f0f2; background:#1a1a1e; border:1px solid #555; "
    "border-radius:4px; padding:4px 8px; font-size:14px; selection-background-color:#4a6fa5;"
)


class SettingsDialog(QDialog):
    """Frameless settings; every change saves + emits changed. Esc / × closes."""

    changed = Signal(object)  # AppSettings

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("程序设置")
        self.setModal(False)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumWidth(320)
        self.setMinimumHeight(260)
        self._settings = settings
        self._block = False

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
        self.title_bar.set_title_text("程序设置")
        self.title_bar.close_clicked.connect(self.close)
        self.title_bar.drag_started.connect(lambda: None)
        lay.addWidget(self.title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # --- opacity ---
        op_row = QWidget()
        op_lay = QHBoxLayout(op_row)
        op_lay.setContentsMargins(0, 0, 0, 0)
        op_lay.setSpacing(8)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(40, 100)
        self.opacity_slider.setSingleStep(1)
        self.opacity_slider.setPageStep(5)
        self.opacity_slider.setValue(int(round(settings.opacity * 100)))
        self.opacity_slider.setMinimumWidth(120)

        self.opacity_value = QLabel(f"{settings.opacity:.2f}")
        self.opacity_value.setObjectName("SettingsValue")
        self.opacity_value.setFixedWidth(52)
        self.opacity_value.setMinimumHeight(28)
        self.opacity_value.setAlignment(Qt.AlignCenter)
        self.opacity_value.setStyleSheet(_VALUE_LABEL_SS)

        op_lay.addWidget(self.opacity_slider, 1)
        op_lay.addWidget(self.opacity_value)

        op_label = QLabel("不透明度")
        op_label.setObjectName("SettingsLabel")
        form.addRow(op_label, op_row)

        # --- cache count: numeric SpinBox only (no slider — live-drag was wiping values) ---
        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(1, 200)
        self.cache_spin.setSingleStep(1)
        self.cache_spin.setValue(int(settings.cache_count))
        self.cache_spin.setMinimumHeight(28)
        self.cache_spin.setMinimumWidth(100)
        self.cache_spin.setAlignment(Qt.AlignCenter)
        self.cache_spin.setStyleSheet(_LINE_EDIT_SS)
        self.cache_spin.setToolTip("计算时最多保留的方案数（默认 50，范围 1–200）")
        # Digits only — QSpinBox; no unit suffix
        self.cache_spin.setSuffix("")

        cache_label = QLabel("预存结果数")
        cache_label.setObjectName("SettingsLabel")
        form.addRow(cache_label, self.cache_spin)

        # --- port: visible LineEdit ---
        self.port_edit = QLineEdit(str(int(settings.bridge_port)))
        self.port_edit.setValidator(QIntValidator(1024, 65535, self))
        self.port_edit.setMinimumHeight(28)
        self.port_edit.setAlignment(Qt.AlignCenter)
        self.port_edit.setStyleSheet(_LINE_EDIT_SS)
        self.port_edit.setPlaceholderText("1024–65535")
        port_label = QLabel("桥接端口")
        port_label.setObjectName("SettingsLabel")
        form.addRow(port_label, self.port_edit)

        self.chk_open_tip = QCheckBox("显示软件使用提示")
        self.chk_open_tip.setChecked(bool(settings.show_open_tip))
        self.chk_open_tip.setToolTip("取消勾选后，点「一键开启窝牌」不再弹出使用说明；再勾选可重新显示")
        self.chk_open_tip.setStyleSheet(
            "QCheckBox { color:#f0f0f4; background:transparent; font-size:13px; spacing:8px; }"
            "QCheckBox::indicator { width:16px; height:16px; border:1px solid #7a7a88;"
            "  border-radius:3px; background:#1c1c1f; }"
            "QCheckBox::indicator:checked { background:#5a8fd4; border:1px solid #8ab4f8; }"
        )
        tip_label = QLabel("使用提示")
        tip_label.setObjectName("SettingsLabel")
        form.addRow(tip_label, self.chk_open_tip)

        hint = QLabel("改动即时保存。Esc 或右上角 × 关闭。")
        hint.setObjectName("SettingsHint")
        hint.setWordWrap(True)

        self.btn_logs = QPushButton("打开日志文件夹")
        self.btn_logs.setObjectName("ModeBtn")
        self.btn_logs.clicked.connect(self._open_logs)

        body.addLayout(form)
        body.addWidget(hint)
        body.addWidget(self.btn_logs)
        lay.addLayout(body)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.reject)

        self.opacity_slider.valueChanged.connect(self._on_opacity)
        self.cache_spin.valueChanged.connect(self._on_cache)
        self.port_edit.editingFinished.connect(self._on_port)
        self.port_edit.textChanged.connect(self._on_port_text)
        self.chk_open_tip.toggled.connect(self._on_open_tip)

        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self._acrylic_done = False
        # Semi-transparent panel so acrylic blur matches main window
        self.setStyleSheet("QDialog#SettingsDialog { background: transparent; }")
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
            self.setWindowOpacity(float(self._settings.opacity))
        except Exception:
            pass
        self._acrylic_done = False
        QTimer.singleShot(0, self._try_acrylic)

    def _try_acrylic(self):
        if self._acrylic_done:
            return
        try:
            hwnd = int(self.winId())
            try_enable_acrylic(hwnd, int(float(self._settings.opacity) * 255))
            self._acrylic_done = True
        except Exception:
            log.exception("settings acrylic")

    def reject(self):
        log.debug("settings closed")
        super().reject()

    def closeEvent(self, event):
        log.debug("settings closeEvent")
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_logs(self):
        path = open_logs_folder()
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        log.info("open logs folder %s ok=%s", path, ok)

    def _on_opacity(self, v: int):
        self.opacity_value.setText(f"{v / 100.0:.2f}")
        try:
            self.setWindowOpacity(float(v) / 100.0)
        except Exception:
            pass
        self._acrylic_done = False
        QTimer.singleShot(0, self._try_acrylic)
        self._persist()

    def _on_cache(self, v: int):
        self._persist()

    def _on_port_text(self, _t: str):
        # live persist only when valid
        t = self.port_edit.text().strip()
        if t.isdigit() and 1024 <= int(t) <= 65535:
            self._persist()

    def _on_port(self):
        t = self.port_edit.text().strip()
        if not t.isdigit() or not (1024 <= int(t) <= 65535):
            self.port_edit.setText(str(self._settings.bridge_port))
            return
        self._persist()

    def current_settings(self) -> AppSettings:
        port_txt = self.port_edit.text().strip()
        try:
            port = int(port_txt) if port_txt else self._settings.bridge_port
        except ValueError:
            port = self._settings.bridge_port
        return AppSettings(
            opacity=float(self.opacity_slider.value()) / 100.0,
            cache_count=int(self.cache_spin.value()),
            bridge_port=port,
            always_on_top=self._settings.always_on_top,
            show_open_tip=bool(self.chk_open_tip.isChecked()),
            enabled_combat_mods=list(self._settings.enabled_combat_mods or []),
        ).clamp()

    def _on_open_tip(self, _checked: bool):
        self._persist()

    def _persist(self, *_args):
        if self._block:
            return
        new_s = self.current_settings()
        self._settings = new_s
        try:
            save_settings(new_s)
            log.info(
                "settings saved opacity=%.2f cache=%s port=%s",
                new_s.opacity,
                new_s.cache_count,
                new_s.bridge_port,
            )
        except Exception:
            log.exception("save_settings failed")
        self.changed.emit(new_s)

    def result_settings(self) -> AppSettings:
        return self.current_settings()
