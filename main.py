#!/usr/bin/env python3
"""Pacific Front Helper — entry point."""
from __future__ import annotations

import os
import sys

# Ensure project root on path (source & frozen)
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows console UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> int:
    from app.logutil import setup_logging, get_logger

    setup_logging()
    log = get_logger("main")
    log.info("starting 窝牌计算器 (frozen=%s)", getattr(sys, "frozen", False))

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle
    from app.ui.main_window import MainWindow
    from app.ui.styles import APP_QSS

    class _FastToolTipStyle(QProxyStyle):
        """Unified short hover delay for QToolTip (settings / mods / sys tips)."""

        _WAKE_MS = 180
        _SLEEP_MS = 200

        def styleHint(self, hint, option=None, widget=None, returnData=None):
            if hint == QStyle.SH_ToolTip_WakeUpDelay:
                return self._WAKE_MS
            if hint == QStyle.SH_ToolTip_FallAsleepDelay:
                return self._SLEEP_MS
            return super().styleHint(hint, option, widget, returnData)

    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("窝牌计算器")
    app.setApplicationVersion("1.0.0")
    app.setApplicationDisplayName("窝牌计算器")
    app.setOrganizationName("窝牌计算器")
    app.setStyle(_FastToolTipStyle("Fusion"))
    app.setStyleSheet(APP_QSS)

    # Window / taskbar icon (assets/app-icon.ico)
    try:
        from PySide6.QtGui import QIcon
        from app.paths import resource_path
        _ico = resource_path("assets/app-icon.ico")
        if _ico is not None and _ico.is_file():
            app.setWindowIcon(QIcon(str(_ico)))
    except Exception:
        log.exception("setWindowIcon")

    win = MainWindow()
    win.show()
    log.info("event loop")
    code = app.exec()
    log.info("exit code=%s", code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
