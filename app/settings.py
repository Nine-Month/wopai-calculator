"""Persist UI settings next to the EXE."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .mod_catalog import (
    COMBAT_MOD_IDS,
    LEGACY_COMBAT_MOD_IDS,
    default_enabled_mods,
)
from .paths import settings_path


@dataclass
class AppSettings:
    opacity: float = 0.92
    cache_count: int = 50
    bridge_port: int = 17891
    always_on_top: bool = True  # default pinned
    enabled_combat_mods: list[str] = field(default_factory=default_enabled_mods)

    def clamp(self) -> "AppSettings":
        self.opacity = max(0.4, min(1.0, float(self.opacity)))
        self.cache_count = max(1, min(200, int(self.cache_count)))
        self.bridge_port = max(1024, min(65535, int(self.bridge_port)))
        self.always_on_top = bool(self.always_on_top)
        known = set(COMBAT_MOD_IDS)
        legacy = set(LEGACY_COMBAT_MOD_IDS)
        if self.enabled_combat_mods is None:
            # Missing / unset → default all combat mods
            self.enabled_combat_mods = list(COMBAT_MOD_IDS)
        else:
            # Preserve intentional empty list (user unchecked everything)
            cleaned = [m for m in self.enabled_combat_mods if m in known]
            # If user still has the full legacy set selected, auto-enable newly
            # added combat mods (fast-repair / unstoppable) once.
            if cleaned and set(cleaned) >= legacy:
                for m in COMBAT_MOD_IDS:
                    if m not in cleaned:
                        cleaned.append(m)
            self.enabled_combat_mods = cleaned
        return self


def load_settings(path: Path | None = None) -> AppSettings:
    p = path or settings_path()
    if not p.is_file():
        return AppSettings().clamp()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if "enabled_combat_mods" not in raw:
            mods = default_enabled_mods()
        elif not isinstance(raw.get("enabled_combat_mods"), list):
            mods = default_enabled_mods()
        else:
            mods = [str(x) for x in raw["enabled_combat_mods"]]
        s = AppSettings(
            opacity=float(raw.get("opacity", 0.92)),
            cache_count=int(raw.get("cache_count", 50)),
            bridge_port=int(raw.get("bridge_port", 17891)),
            always_on_top=bool(raw.get("always_on_top", True)),
            enabled_combat_mods=mods,
        )
        return s.clamp()
    except Exception:
        return AppSettings().clamp()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    p = path or settings_path()
    settings.clamp()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p
