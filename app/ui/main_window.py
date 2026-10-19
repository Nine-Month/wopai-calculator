"""Main helper window."""
from __future__ import annotations

import json
import time as _time

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auto_session import AutoSession
from ..bridge.server import BridgeServer
from ..cdp_attach import GAME_HOME_URL, launch_ie, one_click_connect
from ..logutil import get_logger
from ..paths import data_dir
from ..settings import load_settings, save_settings
from ..worker import (
    MODE_NO_ITEM,
    MODE_ITEMS,
    MODE_LABELS,
    build_scenario_from_snapshot,
    persist_snapshot,
    solution_to_deploy_slots,
    start_solve_thread,
)
from .auto_widgets import AutoProgressPanel, AutoSummaryDialog
from .formation_card import FormationCard
from .mod_filter_dialog import ModFilterDialog
from .resize_helper import ResizeHelper
from .settings_dialog import SettingsDialog
from .styles import APP_QSS
from .soft_hover import install_soft_hover
from .solve_progress import SolveProgressBar
from .title_bar import TitleBar
from .win_acrylic import try_disable_acrylic, try_enable_acrylic, try_solid_accent
from ..win_mouse import execute_moves

log = get_logger("ui")

class _CdpWorker(QObject):
    finished = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, script: str, already_connected=None):
        super().__init__()
        self._script = script
        self._already_connected = already_connected

    def run(self):
        try:
            ok, msg = one_click_connect(
                self._script,
                allow_launch=True,
                progress=lambda m: self.progress.emit(m),
                already_connected=self._already_connected,
            )
            self.finished.emit(ok, msg)
        except Exception as e:
            log.exception("cdp worker")
            self.finished.emit(False, str(e))


class _MouseDeployWorker(QObject):
    finished = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, moves: list):
        super().__init__()
        self._moves = moves or []

    def run(self):
        try:
            ok, msg = execute_moves(
                self._moves,
                between_ms=0.07,
                progress=lambda m: self.progress.emit(m),
            )
            self.finished.emit(ok, msg)
        except Exception as e:
            log.exception("mouse deploy")
            self.finished.emit(False, str(e))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.resize(320, 560)
        self.setMinimumSize(260, 400)
        self.setMouseTracking(True)
        try:
            from PySide6.QtGui import QIcon
            from ..paths import resource_path
            _ico = resource_path("assets/app-icon.ico")
            if _ico is not None and _ico.is_file():
                self.setWindowIcon(QIcon(str(_ico)))
        except Exception:
            pass
        QTimer.singleShot(0, self._place_window_right)

        self.settings = load_settings()
        self.snapshot: dict | None = None
        self.scenario: dict | None = None
        self.solutions: list[dict] = []
        self._solve_thread = None
        self._solve_worker = None
        self._deploying = False
        self._bridge: BridgeServer | None = None
        self._acrylic_done = False
        self._acrylic_ok = False
        self._dragging = False
        self._saved_opacity = self.settings.opacity
        self._resize = ResizeHelper(self, margin=8, enabled=False)  # no edge resize cursor
        self._cdp_thread: QThread | None = None
        self._cdp_worker: _CdpWorker | None = None
        self._mouse_thread: QThread | None = None
        self._mouse_worker: _MouseDeployWorker | None = None
        self._refresh_wait_gen: int = 0
        self._settings_dlg: SettingsDialog | None = None
        self._pending_solve_mode: str | None = None
        self._pending_solve_gen: int = 0
        self._page_connected: bool = False
        self._solve_started_at: float | None = None
        self._solve_last_pct: int = -1
        self._solve_last_msg: str = ""
        self._cards_collapsed: bool = True  # default: all cards folded
        self._pulse_on = False
        self._auto_session: AutoSession | None = None
        self._auto_active = False
        self._closing = False
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(1000)
        self._auto_timer.timeout.connect(self._tick_auto_elapsed)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(400)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(45)
        self._pulse_timer.timeout.connect(self._pulse_progress_bar)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        self.panel = QFrame()
        self.panel.setObjectName("Root")
        self.panel.setStyleSheet(APP_QSS)
        self.panel.setMouseTracking(True)
        panel_lay = QVBoxLayout(self.panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.setSpacing(0)
        root.addWidget(self.panel)

        self.title_bar = TitleBar(show_max=False)
        self.title_bar.pin_toggled.connect(self._on_pin)
        self.title_bar.settings_clicked.connect(self._open_settings)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self.close)
        self.title_bar.drag_started.connect(self._on_drag_started)
        self.title_bar.drag_ended.connect(self._on_drag_ended)
        panel_lay.addWidget(self.title_bar)

        self.conn_badge = QLabel("● 未连接")
        self.conn_badge.setObjectName("ConnBadge")
        self.conn_badge.setAlignment(Qt.AlignCenter)
        panel_lay.addWidget(self.conn_badge)

        top_btns = QHBoxLayout()
        top_btns.setContentsMargins(8, 4, 8, 4)
        top_btns.setSpacing(6)

        self.btn_oneclick = QPushButton("一键开启窝牌")
        self.btn_oneclick.setObjectName("PrimaryBtn")
        self.btn_oneclick.setMinimumHeight(34)
        self.btn_oneclick.setMaximumHeight(34)
        self.btn_oneclick.setToolTip("打开游戏网站（IE 优先）")
        self.btn_oneclick.clicked.connect(self._on_one_click)

        self.btn_scrape = QPushButton("刷新数据")
        self.btn_scrape.setObjectName("SecondaryBtn")
        self.btn_scrape.setMinimumHeight(34)
        self.btn_scrape.setMaximumHeight(34)
        self.btn_scrape.clicked.connect(self._request_scrape)

        top_btns.addWidget(self.btn_oneclick, 2)
        top_btns.addWidget(self.btn_scrape, 1)
        panel_lay.addLayout(top_btns)

        fallback = QHBoxLayout()
        fallback.setContentsMargins(8, 0, 8, 4)
        fallback.setSpacing(6)
        self.btn_copy = QPushButton("复制注入脚本")
        self.btn_copy.setToolTip("复制脚本后到游戏页 F12 控制台粘贴回车")
        self.btn_copy.clicked.connect(self._copy_inject)
        self.btn_fold_all = QPushButton("全部展开")
        self.btn_fold_all.setToolTip("折叠/展开全部阵容卡")
        self.btn_fold_all.clicked.connect(self._toggle_fold_all)
        fallback.addWidget(self.btn_copy, 2)
        fallback.addWidget(self.btn_fold_all, 1)
        panel_lay.addLayout(fallback)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_host.setObjectName("ListHost")
        self.list_host.setStyleSheet("background-color:#2e2e34;")
        self.scroll.setStyleSheet("QScrollArea{background-color:#2e2e34;border:none;}")
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(8, 4, 8, 4)
        self.list_lay.setSpacing(6)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        self.auto_panel = AutoProgressPanel()
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.scroll)
        self.view_stack.addWidget(self.auto_panel)
        self.view_stack.setCurrentIndex(0)
        panel_lay.addWidget(self.view_stack, 1)

        # Fixed 2-line system-message strip ABOVE mode buttons (never shrinks)
        self.sys_msg = QLabel("未启动桥接")
        self.sys_msg.setObjectName("StatusLine")
        self.sys_msg.setWordWrap(True)
        # Two lines of text; never elide with "..."
        try:
            self.sys_msg.setTextElideMode(Qt.TextElideMode.ElideNone)
        except Exception:
            try:
                self.sys_msg.setTextElideMode(Qt.ElideNone)
            except Exception:
                pass
        # ~2 lines at 12px + padding (was too short → looked like one line / "...")
        _sys_h = 48
        self.sys_msg.setFixedHeight(_sys_h)
        self.sys_msg.setMinimumHeight(_sys_h)
        self.sys_msg.setMaximumHeight(_sys_h)
        self.sys_msg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sys_msg.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.sys_msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        panel_lay.addWidget(self.sys_msg)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 6, 8, 4)
        bottom.setSpacing(6)
        # Left: 计算按钮恢复原高度 44；下方「自动化窝牌」等宽；
        # 右侧道具/停止各为 (计算+自动化) 高度之和的一半
        _calc_h = 44
        _auto_h = 22
        _row_gap = 4
        _side_h = (_calc_h + _auto_h) // 2  # 33
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(_row_gap)
        row_calc = QHBoxLayout()
        row_calc.setContentsMargins(0, 0, 0, 0)
        row_calc.setSpacing(6)
        self.btn_no = QPushButton("无道具计算")
        self.btn_items = QPushButton("带道具计算")
        for b, mode in (
            (self.btn_no, MODE_NO_ITEM),
            (self.btn_items, MODE_ITEMS),
        ):
            b.setObjectName("ModeBtn")
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setFixedHeight(_calc_h)
            b.clicked.connect(lambda checked=False, m=mode: self._start_solve(m))
            row_calc.addWidget(b)
        left_col.addLayout(row_calc)
        self.btn_auto = QPushButton("自动化窝牌")
        self.btn_auto.setObjectName("AutoBtn")
        self.btn_auto.setToolTip("按规则选点、计算、放牌并开战。停止计算可中止并弹出汇总。")
        self.btn_auto.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_auto.setFixedHeight(_auto_h)
        self.btn_auto.clicked.connect(self._on_auto_play)
        left_col.addWidget(self.btn_auto)
        bottom.addLayout(left_col, 1)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(_row_gap)
        self.btn_mods = QPushButton("道具")
        self.btn_mods.setObjectName("ModFilterBtn")
        self.btn_mods.setToolTip("筛选「带道具计算」要穷尽的战斗道具（默认全选）")
        self.btn_mods.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_mods.setFixedSize(68, _side_h)
        self.btn_mods.clicked.connect(self._open_mod_filter)
        self.btn_stop = QPushButton("停止计算")
        self.btn_stop.setObjectName("StopBtn")
        self.btn_stop.setToolTip("终止当前求解或自动化（停止后弹出汇总）")
        self.btn_stop.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_stop.setFixedSize(68, _side_h)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_solve)
        side.addWidget(self.btn_mods)
        side.addWidget(self.btn_stop)
        bottom.addLayout(side)
        panel_lay.addLayout(bottom)

        # Progress bar: pct / elapsed / ETA drawn ON the bar only (not system tips)
        prog_wrap = QVBoxLayout()
        prog_wrap.setContentsMargins(8, 0, 8, 8)
        prog_wrap.setSpacing(2)
        self.progress_bar = SolveProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_bar.hide()
        prog_wrap.addWidget(self.progress_bar)
        panel_lay.addLayout(prog_wrap)

        self._apply_opacity()
        self._set_connected_ui(False)
        # Default always-on-top (settings.always_on_top defaults True)
        if self.settings.always_on_top:
            self.title_bar.set_pinned(True)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.setWindowFlag(Qt.FramelessWindowHint, True)
            self.setWindowFlag(Qt.Window, True)

        install_soft_hover(self)
        QTimer.singleShot(200, self._ensure_bridge)
        QTimer.singleShot(0, self._try_load_last)
        log.info("MainWindow ready")

    # ----- unified system message (only above mode buttons) -----
    def _set_status(self, text: str):
        msg = (text or "").strip()
        self.sys_msg.setText(msg)
        # Fixed 2-line strip may clip; hover shows full text in styled tooltip
        self.sys_msg.setToolTip(msg if msg else "")

    def _toast(self, text: str):
        # Same strip — no second toast location
        if text:
            self._set_status(text)

    # ----- drag: kill acrylic / opacity thrash -----
    def _on_drag_started(self):
        if self._dragging:
            return
        self._dragging = True
        log.debug("drag start")
        self._saved_opacity = self.windowOpacity()
        try:
            self.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            hwnd = int(self.winId())
            try_disable_acrylic(hwnd)
            try_solid_accent(hwnd, 255)
        except Exception:
            pass

    def _on_drag_ended(self):
        if not self._dragging:
            return
        self._dragging = False
        log.debug("drag end")
        self._apply_opacity()
        self._acrylic_done = False
        QTimer.singleShot(0, self._try_acrylic)

    # ----- frameless resize -----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._resize.begin(event.globalPosition().toPoint(), event.position().toPoint()):
                self._on_drag_started()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize.active:
            if self._resize.move(event.globalPosition().toPoint()):
                event.accept()
                return
        else:
            self._resize.update_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize.end():
            self._on_drag_ended()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._resize.active:
            self.unsetCursor()
        super().leaveEvent(event)

    # ----- bridge -----
    def _ensure_bridge(self):
        if self._bridge:
            return
        try:
            self._bridge = BridgeServer(port=self.settings.bridge_port)
            self._bridge.signals.client_connected.connect(self._on_bridge_clients)
            self._bridge.signals.client_disconnected.connect(self._on_bridge_clients)
            self._bridge.signals.snapshot_received.connect(self._on_snapshot)
            self._bridge.signals.deploy_result.connect(self._on_deploy_result)
            self._bridge.signals.deploy_plan.connect(self._on_deploy_plan)
            self._bridge.signals.auto_result.connect(self._on_auto_result)
            self._bridge.signals.status.connect(self._set_status)
            self._bridge.signals.error.connect(self._on_bridge_error)
            self._bridge.start()
            self._set_status("桥接就绪 · 点「复制注入脚本」后到游戏页粘贴")
            log.info("bridge listening on %s", self.settings.bridge_port)
        except Exception as e:
            self._bridge = None
            log.exception("bridge start failed")
            self._set_status(f"桥接启动失败: {e}")
            self._alert(f"无法启动本地桥接:\n{e}")

    def _restart_bridge(self):
        if self._bridge:
            try:
                self._bridge.stop()
            except Exception:
                pass
            self._bridge = None
        self._ensure_bridge()

    def _set_connected_ui(self, connected: bool):
        if connected:
            self.conn_badge.setText("● 已连接")
            self.conn_badge.setProperty("connected", True)
        else:
            self.conn_badge.setText("● 未连接")
            self.conn_badge.setProperty("connected", False)
        self.conn_badge.style().unpolish(self.conn_badge)
        self.conn_badge.style().polish(self.conn_badge)

    def _on_bridge_clients(self, n: int):
        self._page_connected = bool(n)
        extra = ""
        if self.scenario:
            meta = self.scenario.get("meta") or {}
            kp = meta.get("keypoint_name") or meta.get("keypoint_id") or "?"
            enemies = ", ".join(e.get("name", "?") for e in (self.scenario.get("enemies") or [])[:4])
            extra = f" · {kp} · {enemies}"
        self._set_connected_ui(self._page_connected)
        if n:
            self._set_status(f"已连接页面 ×{n}{extra}")
            log.info("clients=%s", n)
        else:
            self._set_status(f"未连接{extra} · 点「复制注入脚本」后到游戏页粘贴")
            log.info("clients=0")

    def _on_bridge_error(self, msg: str):
        log.error("bridge error: %s", msg)
        self._set_status(f"桥接错误: {msg}")

    def _on_one_click(self):
        """Open the official game website immediately; tip is informational only."""
        self._open_game_site()
        if bool(getattr(self.settings, "show_open_tip", True)):
            self._confirm_open_browser_tip()

    def _confirm_open_browser_tip(self) -> bool:
        """Show browser tip (frameless dark); Persist「不再显示」."""
        tip = (
            "推荐使用自己常用的浏览器开启活动页面（优先使用谷歌Chrome），"
            "IE浏览器可能会出现问题。成功登入并正常进入活动页面后请点击"
            "「复制注入脚本」按钮，在活动页面按F12进入控制台进行注入。"
        )
        dlg = QDialog(self)
        dlg.setObjectName("OpenTipDialog")
        dlg.setModal(True)
        flags = Qt.FramelessWindowHint | Qt.Dialog
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint):
            flags |= Qt.WindowStaysOnTopHint
        dlg.setWindowFlags(flags)
        dlg.setAttribute(Qt.WA_TranslucentBackground, True)
        dlg.setStyleSheet("QDialog#OpenTipDialog { background: transparent; }")

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("OpenTipPanel")
        panel.setStyleSheet(
            "QFrame#OpenTipPanel {"
            "  background-color: rgba(32, 32, 36, 245);"
            "  border: 1px solid rgba(160, 160, 175, 0.35);"
            "  border-radius: 10px;"
            "}"
            "QLabel#OpenTipTitle {"
            "  color: #f5f5f8;"
            "  font-size: 15px;"
            "  font-weight: 700;"
            "  background: transparent;"
            "}"
            "QLabel#OpenTipBody {"
            "  color: #f0f0f4;"
            "  font-size: 14px;"
            "  background: transparent;"
            "  line-height: 1.45;"
            "}"
            "QCheckBox#OpenTipChk {"
            "  color: #e8e8ee;"
            "  font-size: 13px;"
            "  background: transparent;"
            "  spacing: 8px;"
            "}"
            "QCheckBox#OpenTipChk::indicator {"
            "  width: 16px; height: 16px;"
            "  border: 1px solid #8a8a98;"
            "  border-radius: 3px;"
            "  background: #1c1c1f;"
            "}"
            "QCheckBox#OpenTipChk::indicator:checked {"
            "  background: #5a8fd4;"
            "  border: 1px solid #8ab4f8;"
            "}"
            "QPushButton#OpenTipClose {"
            "  min-height: 40px;"
            "  max-height: 40px;"
            "  min-width: 140px;"
            "  font-size: 15px;"
            "  font-weight: 700;"
            "  background-color: rgba(55, 105, 190, 230);"
            "  border: 1px solid rgba(120,170,255,0.5);"
            "  border-radius: 6px;"
            "  color: #f2f6ff;"
            "  padding: 0 24px;"
            "}"
            "QPushButton#OpenTipClose:hover {"
            "  background-color: rgba(70, 125, 210, 240);"
            "}"
            "QPushButton#OpenTipClose:pressed {"
            "  background-color: rgba(40, 85, 160, 245);"
            "}"
        )
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(14)

        title = QLabel("开启提示")
        title.setObjectName("OpenTipTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        lab = QLabel(tip)
        lab.setObjectName("OpenTipBody")
        lab.setWordWrap(True)
        lab.setMinimumWidth(400)
        lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(lab)

        chk = QCheckBox("以后打开软件点此按钮不再显示此提示")
        chk.setObjectName("OpenTipChk")
        lay.addWidget(chk)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("OpenTipClose")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        dlg.adjustSize()
        # center over main window
        try:
            g = self.frameGeometry()
            dg = dlg.frameGeometry()
            dlg.move(
                g.center().x() - dg.width() // 2,
                g.center().y() - dg.height() // 2,
            )
        except Exception:
            pass

        dlg.exec()
        if chk.isChecked():
            self.settings.show_open_tip = False
            try:
                save_settings(self.settings)
            except Exception:
                log.exception("save show_open_tip")
            sd = self._settings_dlg
            if sd is not None and hasattr(sd, "chk_open_tip"):
                try:
                    sd.chk_open_tip.blockSignals(True)
                    sd.chk_open_tip.setChecked(False)
                    sd.chk_open_tip.blockSignals(False)
                    sd._settings = self.settings
                except Exception:
                    pass
        return True

    def _open_game_site(self):
        self._ensure_bridge()
        url = GAME_HOME_URL
        opened = None
        try:
            opened = launch_ie(url)
        except Exception:
            log.exception("launch_ie")
            opened = None
        if opened is None:
            try:
                import webbrowser

                webbrowser.open(url)
                opened = True
                log.info("opened game URL via webbrowser: %s", url)
            except Exception:
                log.exception("webbrowser.open")
                opened = None
        if opened:
            self._set_status("已打开游戏网站 · 登录后点「复制注入脚本」粘贴")
            self._toast("已打开游戏网站")
        else:
            self._alert(f"无法打开游戏网站，请手动访问：\n{url}")


    def _on_cdp_finished(self, ok: bool, msg: str):
        log.info("one-click done ok=%s msg=%s", ok, msg)
        if ok:
            self._set_status(msg)
            self._toast("注入成功，等待页面握手…")
            QTimer.singleShot(800, self._request_scrape_quiet)
            return
        first = (msg or "").split("\n")[0][:140]
        self._set_status(first)
        # IE paste path / soft prompt — keep inject on clipboard for convenience
        if "Internet Explorer" in (msg or "") or "IE" in (msg or "") or "复制注入" in (msg or ""):
            try:
                if self._bridge:
                    QGuiApplication.clipboard().setText(self._bridge.inject_script_text())
                    self._set_status(first + " · 注入脚本已复制")
            except Exception:
                pass
            self._toast(first)
            return
        if "再点一次" in (msg or ""):
            self._toast(first or "请登录游戏后，点「复制注入脚本」粘贴")
        else:
            self._alert(msg)

    def _on_cdp_thread_finished(self):
        t = self._cdp_thread
        self._cdp_thread = None
        self._cdp_worker = None
        if t is not None:
            t.deleteLater()

    def _request_scrape_quiet(self):
        if self._bridge and self._bridge.client_count > 0:
            self._bridge.request_scrape()

    def _copy_inject(self):
        self._ensure_bridge()
        if not self._bridge:
            return
        text = self._bridge.inject_script_text()
        QGuiApplication.clipboard().setText(text)
        self._set_status("已复制 inject.js：游戏页 F12 控制台粘贴回车")
        log.info("copied inject script (%s chars)", len(text))

    def _request_scrape(self):
        self._ensure_bridge()
        if not self._bridge:
            return
        if not self._bridge.request_scrape():
            self._alert("尚未连接游戏页。请先点「复制注入脚本」，在游戏页 F12 粘贴。")
        else:
            self._toast("正在刷新…")
            n = int(self._bridge.client_count or 0)
            self._refresh_wait_gen += 1
            gen = self._refresh_wait_gen
            QTimer.singleShot(8000, lambda g=gen, nn=n: self._on_refresh_timeout(g, nn))
            log.info("scrape requested")

    def _on_snapshot(self, data: dict):
        if self._auto_active and self._auto_session is not None:
            self._auto_session.notify_snapshot(data)
            self.snapshot = data
            try:
                self.scenario = build_scenario_from_snapshot(data)
                persist_snapshot(data, self.scenario)
            except Exception:
                log.exception("auto snapshot parse")
            return
        pending_mode = self._pending_solve_mode
        try:
            self.snapshot = data
            self.scenario = build_scenario_from_snapshot(data)
            persist_snapshot(data, self.scenario)
            meta = self.scenario.get("meta") or {}
            kp = meta.get("keypoint_name") or meta.get("keypoint_id") or "?"
            n_ship = len(self.scenario.get("ships") or [])
            n_enemy = len(self.scenario.get("enemies") or [])
            enemies = " / ".join(
                f"{e.get('name','?')}({e.get('power',0):.0f})"
                for e in (self.scenario.get("enemies") or [])
            )
            gaps = meta.get("gaps") or []
            gap_txt = (" · 缺口:" + ";".join(gaps)) if gaps else ""
            n_clients = int(self._bridge.client_count or 0) if self._bridge else 0
            self._page_connected = n_clients > 0
            self._set_connected_ui(self._page_connected)
            self._refresh_wait_gen += 1
            self._set_status(
                f"采数成功 · {kp} · 舰{n_ship}/敌{n_enemy} · {enemies}{gap_txt}"
            )
            log.info("snapshot ok kp=%s ships=%s enemies=%s", kp, n_ship, n_enemy)
        except Exception as e:
            log.exception("snapshot parse")
            self._set_status(f"采数解析失败: {e}")
            if pending_mode:
                self._pending_solve_mode = None
                self._reset_progress_bar()
                self._set_solving(False)
                self._alert(f"计算前采数解析失败：{e}")
            return

        if pending_mode:
            self._pending_solve_mode = None
            if not self.scenario.get("enemies") or not self.scenario.get("ships"):
                self._reset_progress_bar()
                self._set_solving(False)
                gaps = (self.scenario.get("meta") or {}).get("gaps") or []
                gap_txt = ("\n" + "；".join(gaps)) if gaps else ""
                self._alert(
                    "采数后场景仍不完整（缺敌方或舰船），无法计算。"
                    "请确认在据点布阵页（URL 含 /operations/行动ID/据点ID）。"
                    + gap_txt
                )
                return
            self._begin_solve(pending_mode)

    # ----- solve -----
    def _start_solve(self, mode: str):
        if self._solve_thread and self._solve_thread.isRunning():
            self._toast("正在计算中…")
            return
        if self._pending_solve_mode:
            self._toast("正在采数，请稍候…")
            return

        if self._bridge and self._bridge.client_count > 0:
            self._pending_solve_gen += 1
            gen = self._pending_solve_gen
            self._pending_solve_mode = mode
            self._set_solving(True)
            self._show_progress_indeterminate("采数中…")
            self._set_status(f"计算前采数（{MODE_LABELS.get(mode, mode)}）…")
            log.info("pre-solve scrape mode=%s gen=%s", mode, gen)
            if not self._bridge.request_scrape():
                self._pending_solve_mode = None
                self._reset_progress_bar()
                self._set_solving(False)
                self._alert("无法请求采数：页面未连接。")
                return
            QTimer.singleShot(15000, lambda g=gen: self._on_pre_solve_scrape_timeout(g))
            return

        if not self.scenario:
            self._alert("请先注入脚本并采数")
            return
        if not self.scenario.get("enemies") or not self.scenario.get("ships"):
            self._alert("场景不完整：请先注入脚本并采数（需据点敌方数据）")
            return
        self._begin_solve(mode)

    def _on_pre_solve_scrape_timeout(self, gen: int):
        if self._pending_solve_gen != gen or not self._pending_solve_mode:
            return
        mode = self._pending_solve_mode
        self._pending_solve_mode = None
        self._reset_progress_bar()
        self._set_solving(False)
        self._alert(
            f"计算前采数超时（{MODE_LABELS.get(mode, mode)}）。\n"
            "请确认页面仍打开据点布阵页后，再点计算或「刷新数据」。"
        )

    def _begin_solve(self, mode: str):
        if self._solve_thread and self._solve_thread.isRunning():
            self._toast("正在计算中…")
            return
        if not self.scenario or not self.scenario.get("enemies") or not self.scenario.get("ships"):
            self._alert("场景不完整：缺敌方或舰船，无法计算")
            self._reset_progress_bar()
            self._set_solving(False)
            return

        self._set_solving(True)
        self._solve_started_at = _time.perf_counter()
        self._solve_last_pct = -1
        self._solve_last_msg = ""
        self._show_progress_indeterminate("计算中")
        # Short status only — ETA lives on the progress bar
        self._set_status(f"计算中 · {MODE_LABELS.get(mode, mode)}")
        self._elapsed_timer.start()
        self._pulse_timer.start()
        log.info("solve start mode=%s cache=%s", mode, self.settings.cache_count)
        allowed = None
        if mode == MODE_ITEMS:
            allowed = list(self.settings.enabled_combat_mods or [])
            if not allowed:
                self._elapsed_timer.stop()
                self._pulse_timer.stop()
                self._solve_started_at = None
                self._reset_progress_bar()
                self._set_solving(False)
                self._alert(
                    "未勾选任何战斗道具。\n"
                    "请点右侧「道具」勾选后再算，或改用「无道具」。"
                )
                self._set_status("带道具计算已取消 · 未勾选战斗道具")
                return
        thread, worker = start_solve_thread(
            self,
            self.scenario,
            mode,
            self.settings.cache_count,
            self.snapshot,
            on_progress=self._on_solve_progress,
            on_finished=self._on_solve_finished,
            on_failed=self._on_solve_failed,
            allowed_mods=allowed,
        )
        self._solve_thread = thread
        self._solve_worker = worker
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _on_thread_finished(self):
        self._elapsed_timer.stop()
        self._pulse_timer.stop()
        self._solve_started_at = None
        self._set_solving(False)
        thread = self._solve_thread
        self._solve_thread = None
        self._solve_worker = None
        if thread is not None:
            try:
                thread.finished.disconnect(self._on_thread_finished)
            except (TypeError, RuntimeError):
                pass
            thread.deleteLater()

    def _set_solving(self, busy: bool):
        for b in (self.btn_no, self.btn_items, self.btn_mods, self.btn_scrape, self.btn_auto):
            b.setEnabled(not busy)
        if hasattr(self, "btn_stop"):
            self.btn_stop.setEnabled(bool(busy))

    def _stop_solve(self):
        """Abort the running manual solve, and automation if it is running.

        Stopping automation uses the same button and still raises the summary.
        """
        if self._auto_active and self._auto_session is not None:
            self._auto_session.cancel()
            w = self._solve_worker
            if w is not None:
                try:
                    w.cancel()
                except Exception:
                    pass
            self._set_status("正在停止自动化…")
            log.info("auto stop requested")
            return
        w = self._solve_worker
        if w is None:
            return
        try:
            w.cancel()
        except Exception:
            pass
        self._set_status("正在停止计算…")
        log.info("solve stop requested")

    def _pulse_progress_bar(self):
        if not self.progress_bar.isVisible():
            return
        self.progress_bar.advance_phase(2.0)

    def _show_progress_indeterminate(self, tip: str = ""):
        self.progress_bar.show()
        # Full-width striped animation (not Qt marquee chunk)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(tip or "…")
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()

    def _set_progress_pct(self, pct: int, tip: str = ""):
        self.progress_bar.show()
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()
        if pct < 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat(tip or "…")
            return
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(0, min(100, int(pct))))
        self.progress_bar.setFormat(tip or f"{int(pct)}%")

    def _reset_progress_bar(self):
        self._elapsed_timer.stop()
        self._pulse_timer.stop()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_bar.reset_phase()
        self.progress_bar.hide()

    def _format_eta_short(self, elapsed: float, pct: int) -> str:
        """Remaining time fragment for the progress-bar overlay only."""
        if pct is None or pct < 3 or pct >= 100 or elapsed < 0.4:
            return ""
        total = elapsed * 100.0 / float(pct)
        remain = max(0.0, total - elapsed)
        if remain < 1:
            return "剩余 <1s"
        if remain > 3600:
            return f"剩余 ~{remain/60:.0f}min"
        return f"剩余 ~{remain:.0f}s"

    def _bar_overlay(self, pct: int, elapsed: float) -> str:
        parts = []
        if pct >= 0:
            parts.append(f"{pct}%")
        if elapsed > 0.05:
            parts.append(f"已用 {elapsed:.0f}s")
        eta = self._format_eta_short(elapsed, pct) if pct >= 0 else ""
        if eta:
            parts.append(eta)
        return " · ".join(parts) if parts else "…"

    def _tick_elapsed(self):
        if self._solve_started_at is None:
            return
        elapsed = _time.perf_counter() - self._solve_started_at
        # Keep sys_msg short — do NOT flash ETA there
        base = self._solve_last_msg or "计算中"
        # Strip any accidental time suffixes from worker messages
        for sep in (" · 已用 ", " · 预计剩余 ", " · 剩余 "):
            if sep in base:
                base = base.split(sep)[0]
        short = base.split("：")[0] if "：" in base else base
        if len(short) > 28:
            short = short[:28] + "…"
        if self._solve_last_pct >= 0:
            self._set_status(f"计算中 · {short}")
            self._set_progress_pct(self._solve_last_pct, self._bar_overlay(self._solve_last_pct, elapsed))
        else:
            self._set_status("计算中")
            self._set_progress_pct(-1, self._bar_overlay(-1, elapsed) if elapsed > 0.4 else "计算中")

    def _on_solve_progress(self, msg: str, pct: int = -1):
        self._solve_last_msg = msg or ""
        self._solve_last_pct = int(pct) if pct is not None else -1
        elapsed = 0.0
        if self._solve_started_at is not None:
            elapsed = _time.perf_counter() - self._solve_started_at
        # Short status only — no ETA flash in sys_msg
        base = msg or "计算中"
        for sep in (" · 已用 ", " · 预计剩余 ", " · 剩余 "):
            if sep in base:
                base = base.split(sep)[0]
        short = base if len(base) <= 36 else (base[:36] + "…")
        self._set_status(f"计算中 · {short}" if short else "计算中")
        if pct >= 0:
            self._set_progress_pct(pct, self._bar_overlay(pct, elapsed))
        else:
            tip = self._bar_overlay(-1, elapsed) if elapsed > 0.4 else (short[:20] or "…")
            self._set_progress_pct(pct, tip)

    def _on_solve_finished(self, sols: list, mode: str):
        self.solutions = sols or []
        self._render_list()
        elapsed = ""
        if self._solve_started_at is not None:
            elapsed = f" · 用时 {_time.perf_counter() - self._solve_started_at:.1f}s"
        if not self.solutions:
            last = self._solve_last_msg or ""
            if "已停止" in last:
                self._set_status("已停止计算" + elapsed)
            elif "组后无解" in last:
                # Keep 「计算 N 组后无解」 as the system tip
                tip = last.split(" · ")[0].strip()
                self._set_status(tip + elapsed)
            else:
                self._set_status("计算后无解" + elapsed)
        else:
            self._set_status(
                f"{MODE_LABELS.get(mode, mode)} 完成 · {len(self.solutions)} 条"
                "（点击卡片放牌）"
                + elapsed
            )
        self._set_progress_pct(100, "完成")
        self._pulse_timer.stop()
        QTimer.singleShot(1200, self._reset_progress_bar)
        log.info("solve done mode=%s n=%s", mode, len(self.solutions))
        try:
            (data_dir() / f"last_solutions_{mode}.json").write_text(
                json.dumps(self.solutions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            log.exception("persist solutions")

    def _on_solve_failed(self, err: str):
        log.error("solve failed: %s", err[:500])
        self._set_status("计算失败")
        self._reset_progress_bar()
        self._alert(err[:500])

    def _render_list(self):
        while self.list_lay.count() > 1:
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, sol in enumerate(self.solutions, 1):
            card = FormationCard(sol, i, collapsed=self._cards_collapsed)
            card.clicked.connect(self._on_card_clicked)
            card.fold_changed.connect(lambda *_: self._sync_fold_all_label())
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            install_soft_hover(card)
        self._sync_fold_all_label()

    def _iter_cards(self):
        for i in range(self.list_lay.count()):
            w = self.list_lay.itemAt(i).widget()
            if isinstance(w, FormationCard):
                yield w

    def _sync_fold_all_label(self):
        cards = list(self._iter_cards())
        if not cards:
            self.btn_fold_all.setText("全部折叠")
            return
        if all(c.is_collapsed() for c in cards):
            self._cards_collapsed = True
            self.btn_fold_all.setText("全部展开")
        else:
            self._cards_collapsed = False
            self.btn_fold_all.setText("全部折叠")

    def _toggle_fold_all(self):
        cards = list(self._iter_cards())
        if not cards:
            return
        # If any expanded → collapse all; else expand all
        collapse = any(not c.is_collapsed() for c in cards)
        self._cards_collapsed = collapse
        for c in cards:
            c.set_collapsed(collapse)
        self._sync_fold_all_label()

    # ----- deploy (Redux code place; mouse legacy unused) -----
    def _on_refresh_timeout(self, gen: int, n: int):
        if gen != self._refresh_wait_gen:
            return
        if self._bridge and self._bridge.client_count > 0:
            self._set_status(f"已连接页面 ×{self._bridge.client_count}")
        else:
            self._set_status("未连接 · 点「复制注入脚本」后到游戏页粘贴")

    def _on_card_clicked(self, sol: dict):
        if self._deploying:
            self._toast("放牌进行中，请稍候")
            return
        if self._mouse_thread and self._mouse_thread.isRunning():
            self._toast("上一轮放牌仍在进行…")
            return
        if not self._bridge or self._bridge.client_count <= 0:
            self._alert("页面未连接，无法放牌。请先复制注入脚本并粘贴。")
            return
        slots = solution_to_deploy_slots(sol, self.snapshot)
        filled = sum(1 for s in slots if s)
        if filled <= 0:
            self._alert("该方案没有可放的舰船代码，无法放牌")
            return
        self._deploying = True
        QTimer.singleShot(8000, self._unlock_deploy)
        log.info("request_deploy(redux) slots=%s filled=%s", len(slots), filled)
        self._set_status(f"正在代码放牌（{filled} 路）…")
        ok = self._bridge.request_deploy(slots, clear=True)
        if not ok:
            self._deploying = False
            self._alert("发送放牌指令失败（桥接未就绪）")
            self._set_status("放牌发送失败")

    def _unlock_deploy(self):
        if self._mouse_thread and self._mouse_thread.isRunning():
            return
        self._deploying = False

    def _on_deploy_plan(self, data: dict):
        log.info(
            "deploy_plan ignored (redux-primary) ok=%s moves=%s err=%s",
            data.get("ok"),
            len(data.get("moves") or []),
            data.get("error"),
        )

    def _on_mouse_deploy_finished(self, ok: bool, msg: str):
        self._deploying = False
        log.info("mouse deploy done ok=%s msg=%s", ok, msg)
        if ok:
            text = f"放牌完成（鼠标拖放 · 未开战）· {msg}"
            self._set_status(text)
        else:
            self._set_status(f"放牌失败: {msg}")
            self._alert(f"放牌失败: {msg}")

    def _on_mouse_thread_finished(self):
        t = self._mouse_thread
        self._mouse_thread = None
        self._mouse_worker = None
        if t is not None:
            t.deleteLater()

    def _on_auto_result(self, data: dict):
        if self._auto_active and self._auto_session is not None:
            self._auto_session.notify_auto(data)

    def _on_deploy_result(self, data: dict):
        if self._auto_active and self._auto_session is not None:
            self._auto_session.notify_deploy(data)
            ok = bool(data.get("ok"))
            if ok:
                self._set_status("自动化放牌完成，准备开战")
            else:
                detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
                err = detail.get("error") or data.get("error") or "放牌失败"
                self._set_status(f"自动化放牌失败: {err}")
            return
        detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
        note = str(detail.get("note") or "")
        method = str(data.get("method") or detail.get("method") or "")
        if "awaiting mouse" in note or "mouse-drag" in note:
            return
        ok = bool(data.get("ok"))
        err = detail.get("error") or data.get("error")
        placed = detail.get("placed") or []
        mods_skip = detail.get("modsSkipped") or []
        mods_ok = detail.get("modsPlaced") or []
        log.info(
            "deploy_result ok=%s method=%s placed=%s modsPlaced=%s modsSkipped=%s err=%s",
            ok, method,
            len(placed) if isinstance(placed, list) else placed,
            len(mods_ok) if isinstance(mods_ok, list) else mods_ok,
            len(mods_skip) if isinstance(mods_skip, list) else mods_skip,
            err,
        )
        self._deploying = False
        if ok:
            n = len(placed) if isinstance(placed, list) else 0
            extra = ""
            if isinstance(mods_ok, list) and mods_ok:
                extra += f" · 道具×{len(mods_ok)}"
            if isinstance(mods_skip, list) and mods_skip:
                extra += f" · 道具跳过×{len(mods_skip)}"
            text = f"代码放牌完成（未开战）· {n} 路{extra}"
            self._set_status(text)
            return
        msg = err or "代码放牌失败（未知原因）"
        self._set_status(f"放牌失败: {msg}")
        self._alert(f"放牌失败: {msg}")

    # ----- mod filter -----
    def _open_mod_filter(self):
        # Second click on 道具 closes; button is not checkable (no pressed look)
        dlg = getattr(self, "_mod_filter_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.close()
            return
        dlg = ModFilterDialog(list(self.settings.enabled_combat_mods or []), self)
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint):
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.changed.connect(self._on_mod_filter_changed)
        self._mod_filter_dlg = dlg
        dlg.finished.connect(lambda *_: setattr(self, "_mod_filter_dlg", None))
        log.info("open mod filter")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_mod_filter_changed(self, ids: list):
        self.settings.enabled_combat_mods = list(ids or [])
        save_settings(self.settings)
        n = len(self.settings.enabled_combat_mods)
        self._set_status(f"道具选择：已选 {n} 个")
        log.info("enabled_combat_mods=%s", self.settings.enabled_combat_mods)

    # ----- settings / window -----
    def _open_settings(self):
        # Second click on ⚙ closes; button appearance unchanged
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._settings_dlg.close()
            return
        dlg = SettingsDialog(self.settings, self)
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint):
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.changed.connect(self._on_settings_changed)
        self._settings_dlg = dlg
        dlg.finished.connect(lambda *_: setattr(self, "_settings_dlg", None))
        log.info("open settings")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_settings_changed(self, new_s):
        if self._dragging:
            self.settings = new_s
            return
        old_port = self.settings.bridge_port
        self.settings = new_s
        self._apply_opacity()
        self._acrylic_done = False
        self._try_acrylic()
        if self.settings.bridge_port != old_port:
            log.info("bridge port change %s → %s", old_port, self.settings.bridge_port)
            self._restart_bridge()

    def _apply_opacity(self):
        if self._dragging:
            return
        try:
            self.setWindowOpacity(float(self.settings.opacity))
        except Exception:
            pass

    def _on_pin(self, pinned: bool):
        self.settings.always_on_top = pinned
        save_settings(self.settings)
        log.info("pin=%s", pinned)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, pinned)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Window, True)
        self.show()
        self._acrylic_done = False
        QTimer.singleShot(50, self._try_acrylic)

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _try_load_last(self):
        p = data_dir() / "last_scenario.json"
        if p.is_file():
            try:
                self.scenario = json.loads(p.read_text(encoding="utf-8"))
                snap_p = data_dir() / "last_snapshot.json"
                if snap_p.is_file():
                    self.snapshot = json.loads(snap_p.read_text(encoding="utf-8"))
                meta = self.scenario.get("meta") or {}
                self._set_status(
                    f"已载入上次场景 · {meta.get('keypoint_name') or meta.get('keypoint_id') or '?'}"
                )
                log.info("loaded last scenario")
            except Exception:
                log.exception("load last scenario")

    def _on_auto_play(self):
        """Start the full auto loop: pick, solve, place, and start the battle."""
        if self._auto_active:
            self._toast("自动化已在运行，可点「停止计算」中止")
            return
        if self._solve_thread and self._solve_thread.isRunning():
            self._toast("请先停止当前计算")
            return
        if self._pending_solve_mode:
            self._toast("正在采数，请稍候…")
            return
        self._ensure_bridge()
        if not self._bridge or self._bridge.client_count <= 0:
            self._alert("尚未连接游戏页。请先点「复制注入脚本」，在游戏页 F12 粘贴。")
            return
        self._auto_session = AutoSession(self._bridge, self.settings)
        self._auto_session.progress.connect(self._on_auto_progress)
        self._auto_session.solve_progress.connect(self._on_auto_solve_progress)
        self._auto_session.status.connect(self._set_status)
        self._auto_session.finished_summary.connect(self._on_auto_finished)
        self._auto_active = True
        self._set_solving(True)
        self.view_stack.setCurrentWidget(self.auto_panel)
        self.auto_panel.update_info({
            "name": "读取行动…",
            "enemy_power": None,
            "reason": "",
            "step": "正在刷新据点与 buff 缓存",
            "lobby": {"ops_done": 0, "ops_total": 3, "cleared_kp": 0, "total_kp": 0},
            "token_full": 0,
            "token_missed": 0,
        })
        self.auto_panel.set_elapsed(0)
        self._auto_timer.start()
        self._set_status("自动化窝牌运行中")
        log.info("auto play start")
        if not self._auto_session.start():
            self._auto_active = False
            self._set_solving(False)
            self._auto_timer.stop()
            self.view_stack.setCurrentWidget(self.scroll)
            self._alert("自动化未能启动")

    def _tick_auto_elapsed(self):
        if not self._auto_active or self._auto_session is None:
            return
        self.auto_panel.set_elapsed(self._auto_session.elapsed())

    def _on_auto_progress(self, info: dict):
        if not self._auto_active:
            return
        self.auto_panel.update_info(info or {})
        step = str((info or {}).get("step") or "")
        if step:
            self._set_status(step[:80])

    def _on_auto_solve_progress(self, msg: str, pct: int):
        if not self._auto_active:
            return
        self._set_status((msg or "计算中")[:80])
        if pct is None or int(pct) < 0:
            self._show_progress_indeterminate(msg or "计算中")
        else:
            self._set_progress_pct(int(pct), (msg or "")[:28] or f"{int(pct)}%")

    def _on_auto_finished(self, summary: dict):
        self._auto_active = False
        self._auto_timer.stop()
        self._set_solving(False)
        self._reset_progress_bar()
        self.view_stack.setCurrentWidget(self.scroll)
        if self._closing:
            self._auto_session = None
            return
        reason = str((summary or {}).get("reason") or "结束")
        elapsed = float((summary or {}).get("elapsed_sec") or 0)
        self._set_status(f"自动化{reason} · {int(elapsed)} 秒")
        log.info("auto finished reason=%s", reason)
        dlg = AutoSummaryDialog(summary or {}, self)
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint):
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.exec()
        self._auto_session = None

    def _alert(self, text: str):
        # Full first paragraph into the strip (no hard [:80] cut); hover shows all
        first = (text or "").split("\n")[0].strip()
        self._set_status(first)
        QMessageBox.warning(self, "提示", text)

    def _place_window_right(self) -> None:
        """Put the window on the right, one window-width in from the right edge."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w = self.width()
        h = self.height()
        # one software-width gap from the right edge
        x = geo.x() + geo.width() - (2 * w)
        y = geo.y() + max(0, (geo.height() - h) // 2)
        x = max(geo.x(), min(x, geo.x() + geo.width() - w))
        y = max(geo.y(), min(y, geo.y() + geo.height() - h))
        self.move(x, y)

    def _try_acrylic(self):
        if self._acrylic_done or self._dragging:
            return
        try:
            hwnd = int(self.winId())
            ok = try_enable_acrylic(hwnd, int(self.settings.opacity * 255))
            self._acrylic_ok = bool(ok)
            self._acrylic_done = True
            log.debug("acrylic enable=%s", ok)
        except Exception:
            log.exception("acrylic")

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._try_acrylic)

    def closeEvent(self, event):
        self._closing = True
        log.info("main window closing")
        if self._auto_session is not None:
            try:
                self._auto_session.cancel()
            except Exception:
                pass
            self._auto_active = False
        if self._bridge:
            try:
                self._bridge.stop()
            except Exception:
                pass
        super().closeEvent(event)
