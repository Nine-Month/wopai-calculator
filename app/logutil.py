"""File logger under app_dir()/logs/helper.log (next to EXE when frozen)."""
from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import logs_dir

_LOGGER_NAME = "pf"
_configured = False


def setup_logging(level: int = logging.DEBUG) -> logging.Logger:
    """Idempotent: configure root 'pf' logger with rotating file + stderr."""
    global _configured
    log = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return log

    log.setLevel(level)
    log.handlers.clear()
    log.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        log_path = logs_dir() / "helper.log"
        fh = RotatingFileHandler(
            str(log_path),
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception as e:
        # Still allow console fallback
        sys.stderr.write(f"[pf] log file setup failed: {e}\n")

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    log.addHandler(sh)

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("Uncaught exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _excepthook
    _configured = True
    log.info("Logging started → %s", logs_dir() / "helper.log")
    return log


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}" if not name.startswith(_LOGGER_NAME) else name)
    return logging.getLogger(_LOGGER_NAME)


def open_logs_folder() -> Path:
    """Ensure logs dir exists and return its path (caller may open in explorer)."""
    return logs_dir()
