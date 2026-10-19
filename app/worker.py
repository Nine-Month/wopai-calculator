"""Background solve workers — never block the Qt UI thread."""
from __future__ import annotations

import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from core import scrape, solver
from core.solver import filter_pool, lane_win_pct
from core.items_solve import (
    collect_boss_seeds,
    health_factor_from_meta,
    load_catalog,
    pick_boss_lane,
    solve_mode,
)
from core.solver import parse_scenario

from .mod_names import mods_label
from .paths import data_dir


MODE_NO_ITEM = "no_item"
MODE_PURCHASABLE = "purchasable"  # legacy alias
MODE_UNPURCHASABLE = "unpurchasable"  # legacy alias
MODE_ITEMS = "both"  # single item-solve: pool = user filter ∩ combat mods

class SolveCancelled(Exception):
    """Raised from progress callback when user stops."""


MODE_LABELS = {
    MODE_NO_ITEM: "无道具计算",
    MODE_ITEMS: "带道具计算",
    MODE_PURCHASABLE: "带道具计算",
    MODE_UNPURCHASABLE: "带道具计算",
}

_LOCALE_CACHE: dict | None = None


def load_locale_map() -> dict:
    """Bundled zh-sg name table: {cards: {card_N: 中文}, enemies: {vehicle_N: 中文}}."""
    global _LOCALE_CACHE
    if _LOCALE_CACHE is not None:
        return _LOCALE_CACHE
    from .paths import resource_path, data_dir

    candidates = [
        data_dir() / "locale_zh_sg.json",
        resource_path("data", "locale_zh_sg.json"),
        Path(__file__).resolve().parent.parent / "data" / "locale_zh_sg.json",
    ]
    for pth in candidates:
        try:
            if pth.is_file():
                data = json.loads(pth.read_text(encoding="utf-8"))
                if isinstance(data, dict) and (
                    isinstance(data.get("cards"), dict) or isinstance(data.get("enemies"), dict)
                ):
                    _LOCALE_CACHE = {
                        "cards": dict(data.get("cards") or {}),
                        "enemies": dict(data.get("enemies") or {}),
                    }
                    return _LOCALE_CACHE
        except Exception:
            continue
    _LOCALE_CACHE = {"cards": {}, "enemies": {}}
    return _LOCALE_CACHE


def _normalize_locale_blob(locale: dict | None) -> dict:
    if not isinstance(locale, dict):
        return {"cards": {}, "enemies": {}}
    if "cards" in locale or "enemies" in locale:
        cards = locale.get("cards") if isinstance(locale.get("cards"), dict) else {}
        enemies = locale.get("enemies") if isinstance(locale.get("enemies"), dict) else {}
        # Also accept nested title maps
        if not cards:
            cards = locale.get("cardTitles") if isinstance(locale.get("cardTitles"), dict) else {}
        if not enemies:
            enemies = locale.get("enemyTitles") if isinstance(locale.get("enemyTitles"), dict) else {}
        return {"cards": dict(cards or {}), "enemies": dict(enemies or {})}
    return {
        "cards": dict(locale.get("cardTitles") or {}),
        "enemies": dict(locale.get("enemyTitles") or {}),
    }


def merge_locale_maps(*blobs: dict | None) -> dict:
    out = {"cards": {}, "enemies": {}}
    for b in blobs:
        n = _normalize_locale_blob(b)
        out["cards"].update(n["cards"])
        out["enemies"].update(n["enemies"])
    return out


def resolve_display_name(code_or_name: str | None, table: dict[str, str]) -> str:
    if not code_or_name:
        return "?"
    s = str(code_or_name)
    if s in table:
        return table[s]
    # bare numeric → try card_/vehicle_ prefixes if table uses them
    if s.isdigit():
        for prefix in ("card_", "vehicle_"):
            k = prefix + s
            if k in table:
                return table[k]
    return s


def snapshot_to_parts(snapshot: dict) -> list[dict]:
    """Turn bridge snapshot into scrape.merge_dumps parts."""
    parts: list[dict] = []
    # Bundled locale first so empty page locale cannot wipe names
    bundled = load_locale_map()
    if bundled.get("cards") or bundled.get("enemies"):
        parts.append(bundled)
    for key in ("settings", "inventory", "keypoint", "operation"):
        blob = snapshot.get(key)
        if isinstance(blob, dict):
            parts.append(blob)
    locale = _normalize_locale_blob(snapshot.get("locale") if isinstance(snapshot, dict) else None)
    if locale.get("cards") or locale.get("enemies"):
        parts.append(locale)
    return parts


def build_scenario_from_snapshot(snapshot: dict) -> dict:
    parts = snapshot_to_parts(snapshot)
    if not parts:
        raise ValueError("快照为空")
    return scrape.merge_dumps(parts)


def persist_snapshot(snapshot: dict, scenario: dict) -> Path:
    d = data_dir()
    (d / "last_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (d / "last_scenario.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Also dump individual pieces for items_solve catalog
    for key in ("settings", "inventory", "state", "operation", "keypoint", "locale"):
        if key in snapshot and snapshot[key] is not None:
            (d / f"last_{key}.json").write_text(
                json.dumps(snapshot[key], ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return d


def normalize_solution(sol: dict, mode: str, locale: dict | None = None) -> dict:
    """Ensure UI-facing fields exist on every solution dict."""
    out = dict(sol)
    out["_mode"] = mode
    out.setdefault("supply_spent", 0)
    out.setdefault("lane_mods", [[] for _ in out.get("slots", [])])
    out.setdefault("cost", out.get("cost", 0))
    out.setdefault("all_certain", bool(out.get("all_certain")))
    out.setdefault("total_overflow", float(out.get("total_overflow") or 0))

    loc = merge_locale_maps(load_locale_map(), locale)
    cards = loc.get("cards") or {}
    enemies = loc.get("enemies") or {}

    # 综合 = 四路胜率加总/4（空位按 0%）；最低 = 已放牌中最低一路
    four_pcts: list[float] = [0.0, 0.0, 0.0, 0.0]
    filled_pcts: list[float] = []
    min_lane_ship: str | None = None
    min_lane_wp: float | None = None
    slots = list(out.get("slots") or [])
    while len(slots) < 4:
        slots.append({"status": "empty"})
    out["slots"] = slots

    for i, slot in enumerate(slots):
        if "mods" not in slot:
            mods = []
            if i < len(out["lane_mods"]):
                mods = out["lane_mods"][i] or []
            slot["mods"] = mods
        slot["mods_zh"] = mods_label(slot.get("mods"))

        if slot.get("status") == "empty":
            if i < 4:
                four_pcts[i] = 0.0
            continue

        code = slot.get("ship_code") or slot.get("code")
        if code:
            slot["ship_code"] = code
            slot["ship"] = resolve_display_name(code, cards)
        else:
            slot["ship"] = resolve_display_name(slot.get("ship"), cards)

        enemy_code = slot.get("enemy_code")
        if enemy_code:
            slot["enemy"] = resolve_display_name(enemy_code, enemies)
        else:
            slot["enemy"] = resolve_display_name(slot.get("enemy"), enemies)

        band = slot.get("power_band") or [0, 0]
        ep = float(slot.get("enemy_power") or 0)
        lo = float(band[0]) if len(band) > 0 else 0.0
        hi = float(band[1]) if len(band) > 1 else lo
        wp = lane_win_pct(lo, hi, ep)
        slot["win_pct"] = round(wp, 1)
        if i < 4:
            four_pcts[i] = wp
        filled_pcts.append(wp)
        if min_lane_wp is None or wp < min_lane_wp:
            min_lane_wp = wp
            min_lane_ship = str(slot.get("ship") or "?")

    out["win_rate_avg"] = round(sum(four_pcts) / 4.0, 1)
    if filled_pcts:
        out["win_rate_min"] = round(min(filled_pcts), 1)
        out["win_rate_min_ship"] = min_lane_ship or ""
    else:
        out["win_rate_min"] = 0.0
        out["win_rate_min_ship"] = ""

    # Resolve top-level ship name list too
    if out.get("ship_codes"):
        out["ships"] = [resolve_display_name(c, cards) for c in out["ship_codes"]]
    elif out.get("ships"):
        out["ships"] = [resolve_display_name(n, cards) for n in out["ships"]]
    return out


def sort_solutions(sols: list[dict]) -> list[dict]:
    """Certain first, then by combined win rate (fill prestore when no 全确)."""
    def key(r: dict):
        return (
            0 if r.get("all_certain") else 1,
            -float(r.get("win_rate_avg") or 0),
            -float(r.get("win_rate_min") or 0),
            r.get("supply_spent", 10**9),
            r.get("cost", 10**9),
            -float(r.get("total_overflow") or 0),
        )
    return sorted(sols, key=key)


def run_no_item(scenario: dict, top: int, progress=None) -> list[dict]:
    """progress(msg: str, pct: int) where pct is 0..100 or -1 indeterminate."""
    ships, enemies, meta = parse_scenario(scenario)
    exclude_types = list(scenario.get("banned_types") or meta.get("banned_types") or [])
    if not exclude_types and any(
        s.banned and (s.ship_type or "").lower() == "cruiser" for s in ships
    ):
        exclude_types = ["cruiser"]
    disable_buffs = bool(scenario.get("disable_buffs") or meta.get("disable_buffs"))
    buff_multiplier = float(
        scenario.get(
            "buff_multiplier",
            meta.get("buff_multiplier", 0.0 if disable_buffs else 1.0),
        )
    )
    if progress:
        progress("无道具计算：穷举中…", 2)

    last_done = [0]

    def _cb(done, total, msg):
        last_done[0] = int(done or 0)
        if not progress:
            return
        pct = min(99, int(100 * done / max(total, 1)))
        progress(msg, pct)

    buf = io.StringIO()
    with redirect_stdout(buf):
        sols = solver.solve(
            ships,
            enemies,
            fill_all=True,
            exclude_banned=True,
            exclude_types=exclude_types,
            disable_buffs=disable_buffs,
            buff_multiplier=buff_multiplier,
            progress=_cb,
            top=top,
        )
    out = [normalize_solution(s, MODE_NO_ITEM) for s in sols]
    out = sort_solutions(out)[:top]
    if not out:
        if progress:
            progress(f"计算 {last_done[0]} 组后无解", 100)
    elif progress:
        n_certain = sum(1 for s in out if s.get("all_certain"))
        if n_certain:
            progress(f"无道具计算：找到 {n_certain} 条全确解" + (f"（共 {len(out)} 条）" if len(out) > n_certain else ""), 100)
        else:
            progress(f"无道具计算：无全确，已按综合胜率保留 {len(out)} 条", 100)
    return out


def _settings_inventory_from_data(data: Path, snapshot: dict | None):
    settings_path = data / "last_settings.json"
    inventory_path = data / "last_inventory.json"
    if snapshot:
        # Always overwrite when snapshot carries fresh catalog data
        if snapshot.get("settings") is not None:
            settings_path.write_text(
                json.dumps(snapshot["settings"], ensure_ascii=False), encoding="utf-8"
            )
        if snapshot.get("inventory") is not None:
            inventory_path.write_text(
                json.dumps(snapshot["inventory"], ensure_ascii=False), encoding="utf-8"
            )
    if not settings_path.is_file():
        raise FileNotFoundError("缺少 settings 快照，请先采数")
    return settings_path, inventory_path if inventory_path.is_file() else None


def run_item_mode(
    scenario: dict,
    mode: str,
    top: int,
    snapshot: dict | None = None,
    progress=None,
    allowed_mods: list[str] | None = None,
) -> list[dict]:
    data = data_dir()
    # Ensure scenario on disk
    sc_path = data / "last_scenario.json"
    sc_path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
    settings_path, inventory_path = _settings_inventory_from_data(data, snapshot)

    ships, enemies, meta = parse_scenario(scenario)
    catalog, owned, _eventum = load_catalog(settings_path, inventory_path)
    bl = pick_boss_lane(enemies, None)
    exclude_types = list(meta.get("banned_types") or scenario.get("banned_types") or [])
    pool = filter_pool(ships, exclude_banned=True, exclude_types=exclude_types)
    hf = health_factor_from_meta(meta, scenario)
    needs_u = bool(scenario.get("needs_unstoppable") or meta.get("needs_unstoppable"))
    allow_u = bool(allowed_mods and "unstoppable" in set(allowed_mods))

    if progress:
        progress(f"{MODE_LABELS.get(mode, mode)}：搜集 boss 种子…", 5)

    def _seed_prog(pct, msg):
        if progress:
            progress(msg, int(pct))

    def _mode_prog(pct, msg):
        if progress:
            progress(msg, int(pct))

    buf = io.StringIO()
    with redirect_stdout(buf):
        boss_boards, nperm = collect_boss_seeds(
            pool, enemies, catalog, owned, bl, progress=_seed_prog, health_factor=hf
        )

    if progress:
        progress(
            f"{MODE_LABELS.get(mode, mode)}：种子 {len(boss_boards)}/{nperm}，穷举道具…",
            40,
        )

    # Combined combat pool; user filter (allowed_mods) narrows it.
    # fast-repair / unstoppable only when checked in 道具菜单.
    solve_mode_key = "both"
    with redirect_stdout(buf):
        result = solve_mode(
            solve_mode_key,
            boss_boards,
            enemies,
            catalog,
            owned,
            bl,
            allow_unstoppable=allow_u,
            max_boards_per_fail=None,
            min_certain=3,
            allowed_mods=allowed_mods,
            progress=_mode_prog,
            health_factor=hf,
            needs_unstoppable=needs_u,
        )
    hits = [normalize_solution(h, MODE_ITEMS) for h in result.get("hits") or []]
    scanned = int(result.get("scanned") or 0)
    if not hits:
        if progress:
            progress(f"计算 {scanned} 组后无解", 100)
    elif progress:
        progress(f"带道具计算：找到 {len(hits)} 条全确解", 100)
    return sort_solutions(hits)[:top]



def solution_to_deploy_slots(sol: dict, snapshot: dict | None = None) -> list[dict]:
    """Build deploy payload slots from a solution."""
    inv_health: dict[str, int] = {}
    if snapshot and isinstance(snapshot.get("inventory"), dict):
        inv = snapshot["inventory"]
        if "result" in inv:
            inv = inv["result"]
        for c in inv.get("cards") or []:
            if c.get("code"):
                inv_health[c["code"]] = int(c.get("health") or 3)

    slots = []
    ship_codes = sol.get("ship_codes") or []
    lane_mods = sol.get("lane_mods") or []
    # Prefer per-slot info (handles empty lanes if any)
    sol_slots = sol.get("slots") or []
    n = max(len(ship_codes), len(sol_slots), 4)
    for i in range(n):
        code = None
        mods: list[str] = []
        if i < len(sol_slots) and sol_slots[i].get("status") != "empty":
            slot_i = sol_slots[i]
            code = slot_i.get("ship_code") or slot_i.get("code")
            # Last resort: raw ship field only if it looks like a code
            if not code:
                raw = slot_i.get("ship")
                if isinstance(raw, str) and raw.startswith("card_"):
                    code = raw
            mods = list(slot_i.get("mods") or [])
        if not code and i < len(ship_codes):
            code = ship_codes[i]
        if not mods and i < len(lane_mods):
            mods = list(lane_mods[i] or [])
        if not code:
            slots.append(None)
            continue
        health = int(inv_health.get(code, 3))
        # Prefer solver-recorded post-repair HP on the slot when present
        if i < len(sol_slots) and sol_slots[i].get("hp") is not None:
            try:
                health = int(sol_slots[i]["hp"])
            except Exception:
                pass
        elif "fast-repair" in mods:
            health = min(3, health + 2)
        slots.append({
            "code": code,
            "health": health,
            "modifiers": mods,
        })
    return slots


class SolveWorker(QObject):
    # msg, percent (0..100); use -1 for indeterminate
    progress = Signal(str, int)
    finished = Signal(list, str)  # solutions, mode
    failed = Signal(str)

    def __init__(
        self,
        scenario: dict,
        mode: str,
        top: int,
        snapshot: dict | None = None,
        allowed_mods: list[str] | None = None,
    ):
        super().__init__()
        self.scenario = scenario
        self.mode = mode
        self.top = top
        self.snapshot = snapshot
        self.allowed_mods = list(allowed_mods) if allowed_mods is not None else None
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _prog(self, msg: str, pct: int = -1):
        if self._cancel:
            raise SolveCancelled("用户停止计算")
        self.progress.emit(str(msg), int(pct))

    def run(self):
        try:
            if not self.scenario or not self.scenario.get("enemies") or not self.scenario.get("ships"):
                self.failed.emit("场景不完整：请先连接页面并采数")
                return
            if self.mode == MODE_NO_ITEM:
                sols = run_no_item(self.scenario, self.top, progress=self._prog)
            elif self.mode in (MODE_ITEMS, MODE_PURCHASABLE, MODE_UNPURCHASABLE, "both"):
                sols = run_item_mode(
                    self.scenario,
                    self.mode,
                    self.top,
                    snapshot=self.snapshot,
                    progress=self._prog,
                    allowed_mods=self.allowed_mods,
                )
            else:
                self.failed.emit(f"未知模式: {self.mode}")
                return
            if self._cancel:
                self.progress.emit("已停止计算", 100)
                self.finished.emit([], self.mode)
                return
            if sols:
                self._prog(f"完成：{len(sols)} 条结果", 100)
            # else run_* already emitted 「计算 N 组后无解」
            self.finished.emit(sols, self.mode)
        except SolveCancelled:
            self.progress.emit("已停止计算", 100)
            self.finished.emit([], self.mode)
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.emit(f"{e}\n{tb}")


def start_solve_thread(
    parent: QObject,
    scenario: dict,
    mode: str,
    top: int,
    snapshot: dict | None,
    on_progress,
    on_finished,
    on_failed,
    allowed_mods: list[str] | None = None,
) -> tuple[QThread, SolveWorker]:
    thread = QThread(parent)
    worker = SolveWorker(scenario, mode, top, snapshot, allowed_mods=allowed_mods)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    # Keep thread alive until quit; caller should hold refs
    return thread, worker
