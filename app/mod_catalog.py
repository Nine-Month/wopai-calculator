"""Combat modifier catalog: ids, labels, official CDN icons."""
from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

from .mod_names import MOD_ZH
from .paths import data_dir

# Official hashed assets from adeck build ad60d10b (modifier-BT0Tc_dr.js)
_CDN_BASE = "https://wows-web-static.wgcdn.co/adeck/ad60d10b/assets/"

MOD_ICON_FILES: dict[str, str] = {
    "damage-control-training": "DamageControlTraining-D8LHmW7H.webp",
    "effectiveness": "Effectiveness-xw5w2Vbb.webp",
    "fast-repair": "FastRepair-CdedIdfH.webp",
    "focused": "Focused-CXQyC36B.webp",
    "gunners-training": "GunnersTraining-BPOlrqCC.webp",
    "moral-impact": "MoralImpact-BFOLs_2c.webp",
    "rudder-crew-training": "RudderCrewTraining-Cf19Aa0s.webp",
    "sea-dog": "SeaDog-DUueoj-w.webp",
    "specialization": "Specialization-B15-g4tG.webp",
    "teamwork": "Teamwork-BSI-rM7i.webp",
    "unstoppable": "Unstoppable-BUPabOyd.webp",
}

# Affects combat / deploy search (same as items_solve combat pools)
COMBAT_MOD_IDS: list[str] = [
    "gunners-training",
    "damage-control-training",
    "rudder-crew-training",
    "teamwork",
    "focused",
    "specialization",
    "sea-dog",
    "fast-repair",
    "unstoppable",
]

# Previous default set (before repair/unstoppable). Used to auto-enable new mods
# when the user still has "all legacy combat mods" selected.
LEGACY_COMBAT_MOD_IDS: list[str] = [
    "gunners-training",
    "damage-control-training",
    "rudder-crew-training",
    "teamwork",
    "focused",
    "specialization",
    "sea-dog",
]

PURCHASABLE_COMBAT = [
    "gunners-training",
    "damage-control-training",
    "rudder-crew-training",
    "teamwork",
]
UNPURCHASABLE_COMBAT = [
    "focused",
    "specialization",
    "sea-dog",
    "fast-repair",
    "unstoppable",
]


def mod_icon_url(mid: str) -> str | None:
    name = MOD_ICON_FILES.get(mid)
    return f"{_CDN_BASE}{name}" if name else None


def mod_label(mid: str) -> str:
    return MOD_ZH.get(mid, mid)


def ensure_mod_icon(mid: str) -> Path | None:
    """Prefer bundled / cached official icon; download to data/mod_icons if needed."""
    name = f"{mid}.webp"
    # 1) Writable cache next to EXE
    cache = data_dir() / "mod_icons"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / name
    if dest.is_file() and dest.stat().st_size > 100:
        return dest
    # 2) Bundled under package data/ (PyInstaller / source tree)
    try:
        from .paths import resource_path
        bundled = resource_path("data", "mod_icons", name)
        if bundled.is_file() and bundled.stat().st_size > 100:
            try:
                dest.write_bytes(bundled.read_bytes())
                return dest
            except Exception:
                return bundled
    except Exception:
        pass
    url = mod_icon_url(mid)
    if not url:
        return None
    try:
        with urlopen(url, timeout=20) as resp:
            raw = resp.read()
        if len(raw) < 100:
            return None
        dest.write_bytes(raw)
        return dest
    except Exception:
        return dest if dest.is_file() else None


def default_enabled_mods() -> list[str]:
    return list(COMBAT_MOD_IDS)
