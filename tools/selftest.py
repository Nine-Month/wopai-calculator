#!/usr/bin/env python3
"""Non-GUI self-test for solver wrappers & settings."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.paths import app_dir, data_dir
from app.settings import AppSettings, load_settings, save_settings
from app.worker import run_no_item, normalize_solution
from core.items_solve import evaluate_with_mods, empty_mods, load_catalog, LaneMods
from core.solver import load_scenario

def main():
    assert app_dir() == ROOT or getattr(sys, "frozen", False)
    p = data_dir() / "_selftest_settings.json"
    save_settings(AppSettings(opacity=0.7, cache_count=11), p)
    assert load_settings(p).cache_count == 11

    kp7 = Path("/workspace/pacific-front-solver/scenarios/kp7.json")
    if not kp7.is_file():
        kp7 = ROOT.parent / "pacific-front-solver" / "scenarios" / "kp7.json"
    if kp7.is_file():
        sc = json.loads(kp7.read_text(encoding="utf-8"))
        sols = run_no_item(sc, top=3)
        print("kp7 no-item", len(sols))
        assert len(sols) >= 1
    else:
        print("skip kp7 (missing)")

    ships, enemies, _ = load_scenario(str(kp7) if kp7.is_file() else str(ROOT / "data" / "last_scenario.json"))
    settings = data_dir() / "last_settings.json"
    inv = data_dir() / "last_inventory.json"
    if settings.is_file():
        catalog, owned, _ = load_catalog(settings, inv if inv.is_file() else None)
        r = evaluate_with_mods(ships[:4], enemies, empty_mods(4), catalog, owned, 0)
        assert r.get("valid")
        print("evaluate_with_mods OK", r.get("n_certain"))
    print("SELFTEST OK")

if __name__ == "__main__":
    main()
