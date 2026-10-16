"""Resolve writable app directory next to the EXE (frozen) or project root."""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Directory for settings.json, data/, logs/, and bundled bridge/."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Source: project root (parent of app/)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Locate bundled read-only resources (bridge/inject.js etc.)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    # Prefer next to EXE / project root
    beside = app_dir().joinpath(*parts)
    if beside.exists():
        return beside
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).joinpath(*parts)
    return beside


def data_dir() -> Path:
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return app_dir() / "settings.json"
