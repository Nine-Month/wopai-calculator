"""Modifier labels/descriptions from game locale (zh-sg), not hand-written blurbs."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Fallback only if locale file missing (ids, never invented Chinese blurbs)
_FALLBACK_TITLE = {
    "focused": "focused",
    "specialization": "specialization",
    "sea-dog": "sea-dog",
    "gunners-training": "gunners-training",
    "damage-control-training": "damage-control-training",
    "rudder-crew-training": "rudder-crew-training",
    "teamwork": "teamwork",
    "effectiveness": "effectiveness",
    "moral-impact": "moral-impact",
    "fast-repair": "fast-repair",
    "unstoppable": "unstoppable",
}

# Catalog effect percents (settings API has no text; numbers from game modifier model)
_STAT_BONUS_PCT = {
    "gunners-training": 20,
    "damage-control-training": 20,
    "rudder-crew-training": 20,
}


def _locale_path() -> Path:
    try:
        from .paths import resource_path

        p = resource_path("data", "locale_zh_sg.json")
        if p.is_file():
            return p
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent / "data" / "locale_zh_sg.json"


@lru_cache(maxsize=1)
def _locale() -> dict:
    p = _locale_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mod_entry(mid: str) -> dict:
    mods = _locale().get("modifiers") or {}
    ent = mods.get(mid) or {}
    return ent if isinstance(ent, dict) else {}


def _titles_map() -> dict[str, str]:
    out = dict(_FALLBACK_TITLE)
    for mid, ent in (_locale().get("modifiers") or {}).items():
        if isinstance(ent, dict) and ent.get("title"):
            out[str(mid)] = str(ent["title"])
    return out


# Eager map for `from .mod_names import MOD_ZH`
MOD_ZH = _titles_map()


def mod_label(mid: str) -> str:
    ent = _mod_entry(mid)
    if ent.get("title"):
        return str(ent["title"])
    return MOD_ZH.get(mid, mid)


def mod_desc(mid: str) -> str:
    """Hover: game locale title + description (+ locale bonus template for stat mods)."""
    ent = _mod_entry(mid)
    title = str(ent.get("title") or mod_label(mid))
    desc = str(ent.get("description") or "").strip()
    lines = [title]
    if desc:
        pct = _STAT_BONUS_PCT.get(mid)
        if pct is not None:
            fmt = (_locale().get("strings") or {}).get("IDS_BONUS_PERCENT_POSITIVE") or "+%(amount)s%"
            try:
                bonus = fmt % {"amount": pct}
            except Exception:
                bonus = f"+{pct}%"
            lines.append(f"{desc} {bonus}")
        else:
            lines.append(desc)
    return "\n".join(lines)


def mods_label(mods: list[str] | None) -> str:
    if not mods:
        return ""
    return "+".join(mod_label(m) for m in mods)
