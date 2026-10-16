#!/usr/bin/env python3
"""Self-test: combat mod filter + fast-repair / unstoppable (no UI)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.mod_catalog import (  # noqa: E402
    COMBAT_MOD_IDS,
    LEGACY_COMBAT_MOD_IDS,
    PURCHASABLE_COMBAT,
    UNPURCHASABLE_COMBAT,
)
from app.settings import AppSettings, load_settings, save_settings  # noqa: E402
from core.items_solve import (  # noqa: E402
    LaneMods,
    ModDef,
    apply_repair_to_ship,
    boards_for_fails,
    evaluate_with_mods,
    mode_mod_lists,
    specials_for_ship,
)
from core.solver import Enemy, Ship  # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_mode_mod_lists_all():
    commons, specials = mode_mod_lists("both", allowed_mods=list(COMBAT_MOD_IDS))
    _assert(set(commons) == set(PURCHASABLE_COMBAT), f"commons={commons}")
    _assert(set(specials) == set(UNPURCHASABLE_COMBAT), f"specials={specials}")
    _assert("fast-repair" in specials and "unstoppable" in specials, "missing repair/unstoppable")
    print("PASS 1: 全选 → commons+specials 覆盖全部战斗道具（含修理/势不可挡）")


def test_mode_mod_lists_partial():
    allow = ["teamwork", "focused"]
    commons, specials = mode_mod_lists("both", allowed_mods=allow)
    _assert(commons == ["teamwork"], f"commons={commons}")
    _assert(specials == ["focused"], f"specials={specials}")
    banned = set(COMBAT_MOD_IDS) - set(allow)
    for mid in banned:
        _assert(mid not in commons and mid not in specials, f"leaked {mid}")
    class FakeShip:
        firepower = 50
        survivability = 40
        mobility = 30
        hp = 3

    ships = [FakeShip() for _ in range(4)]
    boards = boards_for_fails(
        4, [0, 1], [2, 3], ships, commons=commons, specials=specials, max_boards=200
    )
    for board in boards:
        for lm in board:
            for mid in lm.ids():
                _assert(mid in allow, f"board leaked {mid}")
    print(f"PASS 2: 只勾 teamwork+focused → 枚举路径无其它道具（boards={len(boards)}）")


def test_mode_mod_lists_empty():
    commons, specials = mode_mod_lists("both", allowed_mods=[])
    _assert(commons == [] and specials == [], f"got {commons}/{specials}")

    class FakeShip:
        firepower = 50
        survivability = 40
        mobility = 30
        hp = 2

    ships = [FakeShip() for _ in range(4)]
    boards = boards_for_fails(
        4, [0, 1], [2, 3], ships, commons=commons, specials=specials, max_boards=50
    )
    _assert(boards == [], f"expected no mod boards, got {len(boards)}")
    print("PASS 3: 全不选 → 道具池为空、不生成道具枚举板")


def test_settings_preserves_empty():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "settings.json"
        s = AppSettings(enabled_combat_mods=[]).clamp()
        _assert(s.enabled_combat_mods == [], f"clamp wiped empty → {s.enabled_combat_mods}")
        save_settings(s, path)
        loaded = load_settings(path)
        _assert(
            loaded.enabled_combat_mods == [],
            f"load wiped empty → {loaded.enabled_combat_mods}",
        )
        path.write_text(json.dumps({"opacity": 0.9, "cache_count": 50, "bridge_port": 17891}))
        loaded2 = load_settings(path)
        _assert(
            set(loaded2.enabled_combat_mods) == set(COMBAT_MOD_IDS),
            f"missing key should default all, got {loaded2.enabled_combat_mods}",
        )
        # Legacy full selection auto-adds new mods
        path.write_text(json.dumps({
            "opacity": 0.9,
            "cache_count": 50,
            "bridge_port": 17891,
            "enabled_combat_mods": list(LEGACY_COMBAT_MOD_IDS),
        }))
        loaded3 = load_settings(path)
        _assert("fast-repair" in loaded3.enabled_combat_mods, "legacy should gain fast-repair")
        _assert("unstoppable" in loaded3.enabled_combat_mods, "legacy should gain unstoppable")
    print("PASS 3b: settings 空列表持久化；缺省全选；旧版全选自动补新道具")


def test_quick_groups():
    selected = set(COMBAT_MOD_IDS)
    selected -= set(UNPURCHASABLE_COMBAT)
    _assert(selected == set(PURCHASABLE_COMBAT), f"after rare-off {selected}")
    selected |= set(UNPURCHASABLE_COMBAT)
    _assert(selected == set(COMBAT_MOD_IDS), f"after rare-on {selected}")
    selected -= set(PURCHASABLE_COMBAT)
    _assert(selected == set(UNPURCHASABLE_COMBAT), f"after buy-off {selected}")
    selected |= set(PURCHASABLE_COMBAT)
    _assert(selected == set(COMBAT_MOD_IDS), f"after buy-on {selected}")
    commons, specials = mode_mod_lists("both", allowed_mods=list(PURCHASABLE_COMBAT))
    _assert(set(commons) == set(PURCHASABLE_COMBAT) and specials == [], "buy-only pool")
    commons, specials = mode_mod_lists("both", allowed_mods=list(UNPURCHASABLE_COMBAT))
    _assert(commons == [] and set(specials) == set(UNPURCHASABLE_COMBAT), "rare-only pool")
    print("PASS 4: 可购买/不可购买快捷全选·全不选语义正确（含修理/势不可挡）")


def test_no_item_untouched():
    commons, specials = mode_mod_lists("purchasable")
    _assert("focused" not in commons + specials, "purchasable pool must not include rares")
    commons, specials = mode_mod_lists("both", allowed_mods=list(COMBAT_MOD_IDS))
    _assert("focused" in specials, "both+filter includes rares when selected")
    src = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    _assert('MODE_NO_ITEM = "no_item"' in src, "MODE_NO_ITEM")
    _assert('MODE_ITEMS = "both"' in src, "MODE_ITEMS")
    _assert(
        "run_no_item(" in src
        and "allowed_mods" not in src.split("def run_no_item")[1].split("def ")[0],
        "run_no_item has no allowed_mods",
    )
    mw = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    _assert(
        "if mode == MODE_ITEMS:" in mw
        and "allowed = list(self.settings.enabled_combat_mods" in mw,
        "filter wired only for items",
    )
    _assert("未勾选任何战斗道具" in mw, "empty selection prompt present")
    _assert("带道具计算" in mw or "带道具" in mw, "items button label")
    _assert("无道具计算" in mw, "no-item button label")
    print("PASS 5: 无道具不碰道具筛选；道具计算才传 allowed_mods；空选有明确提示")


def test_repair_and_unstoppable_filter():
    # unchecked → not in pool
    commons, specials = mode_mod_lists(
        "both", allowed_mods=["teamwork", "focused"]
    )
    _assert("fast-repair" not in specials and "unstoppable" not in specials, "fail")

    # checked → in pool
    commons, specials = mode_mod_lists(
        "both", allowed_mods=["fast-repair", "unstoppable", "teamwork"]
    )
    _assert(commons == ["teamwork"], "fail")
    _assert(set(specials) == {"fast-repair", "unstoppable"}, "specials mismatch")

    class FakeShip:
        firepower = 50
        survivability = 40
        mobility = 30
        hp = 1

    ships = [FakeShip() for _ in range(4)]
    ships[0].hp = 3  # full HP — repair should be pruned for this ship
    boards = boards_for_fails(
        4, [0, 1], [2, 3], ships,
        commons=commons, specials=specials, max_boards=500,
    )
    for board in boards:
        for i, lm in enumerate(board):
            for mid in lm.ids():
                _assert(mid in {"teamwork", "fast-repair", "unstoppable"}, f"leak {mid}")
            if i == 0 and lm.special == "fast-repair":
                raise AssertionError("fast-repair offered on full-HP ship")
    print(f"PASS 6: 勾选才进池；满血舰不枚举快速修理（boards={len(boards)}）")


def test_repair_stat_rescale():
    ship = Ship(
        name="t",
        firepower=80.0,
        survivability=90.0,
        mobility=70.0,
        hp=1,
        code="card_1",
    )
    # HP1 factor 0.8 → HP3 factor 1.0 ⇒ ratio 1.25
    repaired = apply_repair_to_ship(ship, [0.8, 0.9, 1.0], 2)
    _assert(repaired.hp == 3, f"hp={repaired.hp}")
    _assert(abs(repaired.firepower - 100.0) < 1e-6, f"fp={repaired.firepower}")
    _assert(abs(repaired.survivability - 112.5) < 1e-6, f"surv={repaired.survivability}")
    _assert(abs(repaired.mobility - 87.5) < 1e-6, f"mob={repaired.mobility}")

    # Full HP: no change
    full = Ship(name="f", firepower=100, survivability=100, mobility=100, hp=3)
    same = apply_repair_to_ship(full, [0.8, 0.9, 1.0], 2)
    _assert(same is full or (same.hp == 3 and same.firepower == 100), "full HP noop")

    catalog = {
        "fast-repair": ModDef("fast-repair", "special", None, "repair"),
    }
    owned = {"fast-repair": 2}
    enemies = [
        Enemy(name="e", power=50, weaknesses=[]),
        Enemy(name="e2", power=50, weaknesses=[]),
        Enemy(name="e3", power=50, weaknesses=[]),
        Enemy(name="e4", power=50, weaknesses=[]),
    ]
    ships = [
        Ship(name="a", firepower=40, survivability=40, mobility=40, hp=1, code="c1"),
        Ship(name="b", firepower=40, survivability=40, mobility=40, hp=3, code="c2"),
        Ship(name="c", firepower=40, survivability=40, mobility=40, hp=3, code="c3"),
        Ship(name="d", firepower=40, survivability=40, mobility=40, hp=3, code="c4"),
    ]
    board = [
        LaneMods(special="fast-repair"),
        LaneMods(),
        LaneMods(),
        LaneMods(),
    ]
    ev = evaluate_with_mods(
        ships, enemies, board, catalog, owned, 0, health_factor=[0.8, 0.9, 1.0]
    )
    _assert(ev.get("valid"), f"invalid: {ev}")
    _assert(ev["slots"][0]["hp"] == 3, f"slot hp={ev['slots'][0]['hp']}")
    _assert(ev["slots"][0]["hp_before"] == 1, "fail")
    _assert(any(r.get("to_hp") == 3 for r in ev.get("repairs") or []), "repairs missing")
    # Inventory limit: 0 owned → invalid when used
    ev2 = evaluate_with_mods(
        ships, enemies, board, catalog, {}, 0, health_factor=[0.8, 0.9, 1.0]
    )
    _assert(not ev2.get("valid"), "should reject when no repair owned")
    print("PASS 7: 快速修理 +2HP 封顶3、按 HealthFactor 重算；库存不足则无效")


def test_unstoppable_gate():
    commons, specials = mode_mod_lists("both", allowed_mods=["unstoppable"])
    _assert(specials == ["unstoppable"] and commons == [], "fail")
    commons, specials = mode_mod_lists("both", allowed_mods=["focused"])
    _assert("unstoppable" not in specials, "fail")
    # CLI default without allow drops unstoppable
    commons, specials = mode_mod_lists("both")
    _assert("unstoppable" not in specials, "fail")
    commons, specials = mode_mod_lists("both", allow_unstoppable=True)
    _assert("unstoppable" in specials, "fail")
    print("PASS 8: 势不可挡仅在勾选 / --allow-unstoppable 时进入池")


def test_mod_filter_dialog_frameless():
    src = (ROOT / "app" / "ui" / "mod_filter_dialog.py").read_text(encoding="utf-8")
    _assert("FramelessWindowHint" in src, "must be frameless")
    _assert("QDialogButtonBox" not in src, "no OK/Cancel")
    _assert("close_clicked" in src, "× close via TitleBar")
    _assert("fast-repair" in "".join(COMBAT_MOD_IDS) or "fast-repair" in COMBAT_MOD_IDS, "fail")
    mw = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    _assert("_on_mod_filter_changed" in mw, "live-save handler")
    print("PASS 9: 道具菜单 frameless + 无 OK/Cancel + 即时保存")


def main():
    test_mode_mod_lists_all()
    test_mode_mod_lists_partial()
    test_mode_mod_lists_empty()
    test_settings_preserves_empty()
    test_quick_groups()
    test_no_item_untouched()
    test_repair_and_unstoppable_filter()
    test_repair_stat_rescale()
    test_unstoppable_gate()
    test_mod_filter_dialog_frameless()
    print("\nALL MOD-FILTER / REPAIR / UNSTOPPABLE SELF-TESTS PASSED")


if __name__ == "__main__":
    main()
