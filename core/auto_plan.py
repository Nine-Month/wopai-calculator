"""Pure planning for 自动化窝牌: target pick, stage goals, layered solves.

UI and the browser bridge stay outside this module so the rules can be tested
without Qt or a live game page.
"""
from __future__ import annotations

from typing import Any, Callable

from .buff_cache import BuffCache, KpRow, unwrap_operation
from .items_solve import (
    EFFECTS,
    PURCHASABLE_COMBAT,
    UNPURCHASABLE_COMBAT,
    LaneMods,
    ModDef,
    STAT_TRAINING_IDS,
    best_stat_training,
    evaluate_with_mods,
    health_factor_from_meta,
    mode_mod_lists,
    pick_boss_lane,
    solve_mode,
)
from .solver import Ship, evaluate, filter_pool, lane_win_pct, parse_scenario, solve

# Top combined-win decks used by the purchasable passes. Fixed at 50 unless the
# caller passes another positive cap (settings cache_count is a floor for the
# no-item listing, not a replacement for this ladder).
AUTO_TOP_N = 50
EFFECTIVENESS_MOD = "effectiveness"

LAYER_ZH = {
    "no_item": "无道具",
    "purchasable": "可购买道具",
    "unpurchasable": "不可购买道具",
    "purchasable_fallback": "可购买道具（最优可用）",
    "transition": "过渡少算",
}


class AutoCancelled(Exception):
    """Raised when the user stops a layered solve."""


def _check(cancel: Callable[[], bool] | None) -> None:
    if cancel and cancel():
        raise AutoCancelled("用户停止计算")


def is_full_win(sol: dict | None) -> bool:
    if not sol:
        return False
    if sol.get("all_certain"):
        return True
    slots = [s for s in (sol.get("slots") or []) if s.get("status") != "empty"]
    if not slots:
        return False
    if all(s.get("status") == "certain" for s in slots):
        return True
    try:
        return float(sol.get("win_rate_min") or 0) >= 99.95
    except (TypeError, ValueError):
        return False


def annotate_win(sol: dict) -> dict:
    """Fill combined / minimum lane win% with the same discrete formula as the UI.

    综合 = 四路胜率加总/4（空位按 0%）；最低 = 已放牌各路中的最小胜率。
    """
    four: list[float] = []
    filled: list[float] = []
    min_ship = ""
    min_wp: float | None = None
    for i, slot in enumerate(sol.get("slots") or []):
        if slot.get("status") == "empty":
            if i < 4:
                four.append(0.0)
            continue
        band = slot.get("power_band") or [0, 0]
        try:
            lo = float(band[0]) if len(band) > 0 else 0.0
            hi = float(band[1]) if len(band) > 1 else lo
            enemy = float(slot.get("enemy_power") or 0)
        except (TypeError, ValueError):
            lo, hi, enemy = 0.0, 0.0, 0.0
        wp = lane_win_pct(lo, hi, enemy)
        slot["win_pct"] = round(wp, 1)
        if i < 4:
            four.append(wp)
        filled.append(wp)
        if min_wp is None or wp < min_wp:
            min_wp = wp
            min_ship = str(slot.get("ship") or "")
    while len(four) < 4:
        four.append(0.0)
    sol["win_rate_avg"] = round(sum(four[:4]) / 4.0, 1)
    sol["win_rate_min"] = round(min(filled), 1) if filled else 0.0
    sol["win_rate_min_ship"] = min_ship
    sol.setdefault("supply_spent", 0)
    sol.setdefault("lane_mods", [list(s.get("mods") or []) for s in (sol.get("slots") or [])])
    return sol


def _overflow(sol: dict) -> float:
    try:
        return float(sol.get("total_overflow") or 0)
    except (TypeError, ValueError):
        return 0.0


def _win_avg(sol: dict) -> float:
    try:
        return float(sol.get("win_rate_avg") or 0)
    except (TypeError, ValueError):
        return 0.0


def pick_full_win_lowest_overflow(sols: list[dict]) -> dict | None:
    """Among 100% boards, the tightest overflow (省战力，不追求多余溢出)."""
    full = [s for s in sols if is_full_win(s)]
    if not full:
        return None
    return min(
        full,
        key=lambda s: (
            _overflow(s),
            float(s.get("supply_spent") or 0),
            float(s.get("cost") or 0),
            -_win_avg(s),
        ),
    )


def top_by_combined_win(sols: list[dict], n: int = AUTO_TOP_N) -> list[dict]:
    ranked = sorted(
        sols,
        key=lambda s: (
            0 if is_full_win(s) else 1,
            -_win_avg(s),
            -float(s.get("win_rate_min") or 0),
            -_overflow(s),
            float(s.get("cost") or 0),
        ),
    )
    return ranked[: max(1, int(n))]


def pick_best_effort(sols: list[dict]) -> dict | None:
    """Best board we are willing to send when nothing is a clean 100%.

    Full-win boards still prefer the lowest overflow. Partial boards prefer
    higher combined win rate, then higher overflow (closer to a clear).
    """
    if not sols:
        return None

    def key(s: dict):
        full = is_full_win(s)
        return (
            0 if full else 1,
            -_win_avg(s),
            -float(s.get("win_rate_min") or 0),
            _overflow(s) if full else -_overflow(s),
            float(s.get("supply_spent") or 0),
            float(s.get("cost") or 0),
        )

    return min(sols, key=key)


def _unwrap_blob(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    inner = data.get("result")
    if isinstance(inner, dict):
        return inner
    return data


def catalog_from_snapshot(
    snapshot: dict | None,
) -> tuple[dict[str, ModDef], dict[str, int]]:
    """Combat catalog + owned counts from a bridge snapshot.

    Unpurchasable mods have no price: they are usable only when inventory
    already holds them. Purchasable mods can be bought for supply.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    settings = _unwrap_blob(snap.get("settings") if isinstance(snap.get("settings"), dict) else None)
    inventory = _unwrap_blob(snap.get("inventory") if isinstance(snap.get("inventory"), dict) else None)
    owned: dict[str, int] = {}
    for mod in inventory.get("modifiers") or []:
        if isinstance(mod, dict) and mod.get("id"):
            try:
                owned[str(mod["id"])] = int(mod.get("amount") or 0)
            except (TypeError, ValueError):
                owned[str(mod["id"])] = 0
    modifiers = (settings.get("collection") or {}).get("modifiers") if settings else None
    if not modifiers:
        return build_combat_catalog(None, owned)
    catalog: dict[str, ModDef] = {}
    for raw in modifiers:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        mid = str(raw["id"])
        price = None
        if raw.get("price"):
            try:
                price = int(raw["price"][0]["amount"])
            except (TypeError, ValueError, KeyError, IndexError):
                price = None
        group = "common" if raw.get("type") == 1 else "special"
        eff = EFFECTS.get(mid, {"kind": "noop", "note": "unknown"})
        catalog[mid] = ModDef(
            id=mid,
            group=group,
            price=price,
            kind=eff.get("kind") or "noop",
            stat=eff.get("stat"),
            pct=float(eff.get("pct") or 0),
            all_pct=float(eff.get("all_pct") or 0),
            mult=float(eff.get("mult") or 1),
            note=eff.get("note") or "",
        )
    # Fill any combat id the settings blob omitted so the ladder still knows it.
    base, _ = build_combat_catalog()
    for mid, mod in base.items():
        catalog.setdefault(mid, mod)
    return catalog, owned


def build_combat_catalog(
    catalog: dict[str, ModDef] | None = None,
    owned: dict[str, int] | None = None,
) -> tuple[dict[str, ModDef], dict[str, int]]:
    if catalog:
        return catalog, dict(owned or {})
    built: dict[str, ModDef] = {}
    for mid, eff in EFFECTS.items():
        if mid in PURCHASABLE_COMBAT or mid in ("effectiveness", "moral-impact"):
            group, price = "common", 12
        else:
            group, price = "special", None
        built[mid] = ModDef(
            id=mid,
            group=group,
            price=price,
            kind=eff["kind"],
            stat=eff.get("stat"),
            pct=float(eff.get("pct") or 0),
            all_pct=float(eff.get("all_pct") or 0),
            mult=float(eff.get("mult") or 1),
            note=eff.get("note") or "",
        )
    return built, dict(owned or {})


def _ships_by_codes(pool: list[Ship], codes: list) -> list[Ship] | None:
    by = {s.code: s for s in pool if s.code}
    out: list[Ship] = []
    for code in codes:
        ship = by.get(code)
        if ship is None:
            return None
        out.append(ship)
    return out


def _lane_mods_from_solution(sol: dict, n: int, catalog: dict[str, ModDef]) -> list[LaneMods]:
    raw = sol.get("lane_mods") or []
    slots = sol.get("slots") or []
    out: list[LaneMods] = []
    for i in range(n):
        mods: list[str] = []
        if i < len(raw) and raw[i]:
            mods = list(raw[i])
        elif i < len(slots):
            mods = list(slots[i].get("mods") or [])
        common = None
        special = None
        for mid in mods:
            if mid == EFFECTIVENESS_MOD:
                continue
            info = catalog.get(mid)
            if info is None:
                continue
            if info.group == "special" and special is None:
                special = mid
            elif info.group == "common" and common is None:
                common = mid
        out.append(LaneMods(common=common, special=special))
    return out


def apply_purchasable_best_stat(
    ships: list[Ship],
    base_mods: list[LaneMods],
    base_ev: dict,
    enemies,
    catalog: dict[str, ModDef],
    owned: dict[str, int],
    *,
    skip_lanes: set[int] | None = None,
    health_factor: list[float] | None = None,
    boss_lane: int = 0,
) -> dict:
    """One purchasable attachment per seed — not a full purchasable cartesian product.

    自动化可购买层策略（对照 items_solve 的训练剪枝，再收紧）：

    * 不对可购买道具做全组合穷尽。炮术 / 损管 / 舵手 / 协作的笛卡尔积在这里不跑。
    * 每张参战卡只挂载「对该卡自身属性增益最有效的那一档」可购买战斗道具：
      比较该卡生命缩放后的火力、存活性、机动，取数值最高的一项，对应
      ``gunners-training`` / ``damage-control-training`` / ``rudder-crew-training``
      （各 +20%，与 ``best_stat_training`` 一致）。
    * ``teamwork`` 加倍的是这艘船给邻居的 buff，不是这张卡自己的三项属性，
      所以本层不搜索协作。
    * ``skip_lanes``（进度代币目标车道）不挂任何可购买战斗道具。那些车道的
      通用槽要留给行动末据点的「效率提升」（effectiveness），效率提升不参与
      胜率穷尽。
    * 每个种子只评估这一副挂载：原本不是全胜、且不在 skip 里的车道挂上该档；
      已经全胜的车道不挂，避免无谓抬高溢出和补给消耗。
    """
    skip = set(skip_lanes or ())
    lm = [LaneMods(common=m.common, special=m.special) for m in base_mods]
    slots = base_ev.get("slots") or []
    for i, ship in enumerate(ships):
        if i in skip:
            if lm[i].common in STAT_TRAINING_IDS or lm[i].common == "teamwork":
                lm[i] = LaneMods(common=None, special=lm[i].special)
            continue
        status = slots[i].get("status") if i < len(slots) else None
        if status == "certain":
            continue
        training = best_stat_training(ship)
        if training not in catalog:
            continue
        lm[i] = LaneMods(common=training, special=lm[i].special)
    ev = evaluate_with_mods(
        ships,
        enemies,
        lm,
        catalog,
        owned,
        boss_lane,
        health_factor=health_factor,
    )
    if ev.get("valid"):
        ev["_mode"] = "purchasable"
        annotate_win(ev)
    return ev


def attach_effectiveness(sol: dict, lanes: list[int] | set[int]) -> dict:
    """Put 效率提升 on progress-token lanes after the combat solve.

    Effectiveness shares the common (group A) slot with stat training, so any
    purchasable combat common on those lanes is dropped. It is not part of the
    win-rate search — progress-token targets are excluded there on purpose.
    """
    import copy

    out = copy.deepcopy(sol)
    lanes = [int(i) for i in lanes]
    n = max(len(out.get("slots") or []), len(out.get("lane_mods") or []), 4)
    lane_mods = [list(x or []) for x in (out.get("lane_mods") or [])]
    while len(lane_mods) < n:
        lane_mods.append([])
    slots = out.get("slots") or []
    applied: list[int] = []
    for i in lanes:
        if i < 0 or i >= n:
            continue
        if i < len(slots) and slots[i].get("status") == "empty":
            continue
        if i < len(slots) and not (slots[i].get("ship_code") or slots[i].get("ship")):
            continue
        kept = [
            m
            for m in lane_mods[i]
            if m not in STAT_TRAINING_IDS and m not in ("teamwork", EFFECTIVENESS_MOD)
        ]
        kept.append(EFFECTIVENESS_MOD)
        lane_mods[i] = kept
        if i < len(slots):
            slots[i]["mods"] = list(kept)
        applied.append(i)
    out["lane_mods"] = lane_mods
    out["effectiveness_lanes"] = applied
    note = out.get("note") or ""
    extra = "末据点进度代币车道已挂效率提升"
    out["note"] = (note + " · " + extra).strip(" ·") if note else extra
    return out


def cheap_transition_plan(scenario: dict) -> dict:
    """Stage quota already met: one own-lowest-power ship, no search."""
    ships, enemies, meta = parse_scenario(scenario)
    exclude = list(scenario.get("banned_types") or meta.get("banned_types") or [])
    pool = filter_pool(ships, exclude_banned=True, exclude_types=exclude)
    if not pool or not enemies:
        raise ValueError("过渡战斗没有可用舰船或敌方")
    ship = min(pool, key=lambda s: (s.base_sum, s.cost, s.name or ""))
    placed: list[Ship | None] = [None] * len(enemies)
    placed[0] = ship
    disable = bool(scenario.get("disable_buffs") or meta.get("disable_buffs"))
    buff_m = float(
        scenario.get(
            "buff_multiplier",
            meta.get("buff_multiplier", 0.0 if disable else 1.0),
        )
    )
    ev = evaluate(
        placed,
        enemies,
        disable_buffs=disable,
        buff_multiplier=buff_m,
        require_filled=False,
    )
    ev["_mode"] = "transition"
    ev["layer"] = "transition"
    ev["note"] = "阶段摧毁目标已达成，过渡战斗只放一张最低战力牌"
    annotate_win(ev)
    return ev


def _no_item_solutions(
    scenario: dict,
    *,
    top: int,
    cancel: Callable[[], bool] | None,
    progress: Callable[[str, int], None] | None,
) -> list[dict]:
    ships, enemies, meta = parse_scenario(scenario)
    exclude = list(scenario.get("banned_types") or meta.get("banned_types") or [])
    disable = bool(scenario.get("disable_buffs") or meta.get("disable_buffs"))
    buff_m = float(
        scenario.get(
            "buff_multiplier",
            meta.get("buff_multiplier", 0.0 if disable else 1.0),
        )
    )

    def _cb(done, total, msg):
        _check(cancel)
        if progress:
            pct = min(99, int(100 * (done or 0) / max(total or 1, 1)))
            progress(f"无道具计算：{msg}", pct)

    sols = solve(
        ships,
        enemies,
        fill_all=True,
        exclude_banned=True,
        exclude_types=exclude,
        disable_buffs=disable,
        buff_multiplier=buff_m,
        progress=_cb,
        top=top,
    )
    for s in sols:
        s["_mode"] = "no_item"
        s.setdefault("lane_mods", [[] for _ in (s.get("slots") or [])])
        s.setdefault("supply_spent", 0)
        annotate_win(s)
    return sols


def _seed_boards(
    sols: list[dict],
    pool: list[Ship],
    enemies,
    catalog,
    owned,
    health_factor,
    boss_lane: int,
):
    seeds = []
    for sol in sols:
        codes = list(sol.get("ship_codes") or [])
        if len(codes) != len(enemies):
            continue
        ships = _ships_by_codes(pool, codes)
        if not ships:
            continue
        mods = _lane_mods_from_solution(sol, len(enemies), catalog)
        ev = evaluate_with_mods(
            ships, enemies, mods, catalog, owned, boss_lane, health_factor=health_factor
        )
        if not ev.get("valid"):
            continue
        annotate_win(ev)
        seeds.append((ships, mods, ev))
    return seeds


def layered_battle_plan(
    scenario: dict,
    *,
    top_n: int = AUTO_TOP_N,
    progress_lanes: list[int] | None = None,
    attach_effectiveness_lanes: list[int] | None = None,
    allowed_mods: list[str] | None = None,
    snapshot: dict | None = None,
    catalog: dict[str, ModDef] | None = None,
    owned: dict[str, int] | None = None,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Run the four-step battle ladder and return the board to deploy.

    1. No-item. If any board is a full 100%, take the lowest overflow.
    2. Otherwise re-score the top ``top_n`` combined-win boards with the
       single best-stat purchasable training (see ``apply_purchasable_best_stat``).
       Progress-token lanes are not part of that search.
    3. If purchasable still has no all-lane 100%, run unpurchasable combat mods
       on those same top boards (existing ``solve_mode`` specials).
    4. If that also misses 100%, run the purchasable best-stat pass again on the
       top ``top_n`` combined-win boards from step 3 and send the best available
       board even when it is not 100%.
    """
    _check(cancel)
    progress_lanes = [int(i) for i in (progress_lanes or [])]
    skip = set(progress_lanes)
    if catalog is None:
        catalog, owned_snap = catalog_from_snapshot(snapshot)
        if owned is None:
            owned = owned_snap
    else:
        catalog, owned = build_combat_catalog(catalog, owned)
    ships, enemies, meta = parse_scenario(scenario)
    if not enemies or not ships:
        raise ValueError("场景不完整：缺敌方或舰船")
    exclude = list(scenario.get("banned_types") or meta.get("banned_types") or [])
    pool = filter_pool(ships, exclude_banned=True, exclude_types=exclude)
    hf = health_factor_from_meta(meta, scenario)
    boss = pick_boss_lane(enemies, None)
    needs_u = bool(scenario.get("needs_unstoppable") or meta.get("needs_unstoppable"))

    listing = max(int(top_n), AUTO_TOP_N)
    no_item = _no_item_solutions(
        scenario, top=listing, cancel=cancel, progress=progress
    )
    _check(cancel)
    chosen = pick_full_win_lowest_overflow(no_item)
    layer = "no_item"
    pool_for_fallback = list(no_item)

    if chosen is None:
        seeds_src = top_by_combined_win(no_item, top_n)
        if progress:
            progress(f"可购买道具：综合胜率前 {len(seeds_src)} 组（仅最高属性一档）", 40)
        purch_hits: list[dict] = []
        boards = _seed_boards(seeds_src, pool, enemies, catalog, owned, hf, boss)
        for ships_i, mods_i, ev_i in boards:
            _check(cancel)
            hit = apply_purchasable_best_stat(
                ships_i,
                mods_i,
                ev_i,
                enemies,
                catalog,
                owned,
                skip_lanes=skip,
                health_factor=hf,
                boss_lane=boss,
            )
            if hit.get("valid"):
                purch_hits.append(hit)
        chosen = pick_full_win_lowest_overflow(purch_hits)
        if chosen is not None:
            layer = "purchasable"
        else:
            if progress:
                progress("不可购买道具计算…", 60)
            un_allow = list(UNPURCHASABLE_COMBAT)
            if allowed_mods is not None:
                allow = set(allowed_mods)
                un_allow = [m for m in un_allow if m in allow]
            boss_boards = [(sh, ev) for sh, _mods, ev in boards]
            if not boss_boards:
                boss_boards = [
                    (sh, ev) for sh, _mods, ev in _seed_boards(
                        seeds_src, pool, enemies, catalog, owned, hf, boss
                    )
                ]

            def _mode_prog(pct, msg):
                _check(cancel)
                if progress:
                    progress(f"不可购买道具：{msg}", max(60, min(90, int(pct) if pct else 70)))

            result = solve_mode(
                "unpurchasable",
                boss_boards,
                enemies,
                catalog,
                owned,
                boss,
                allow_unstoppable=("unstoppable" in un_allow),
                max_boards_per_fail=None,
                require_boss=False,
                min_certain=0,
                allowed_mods=un_allow,
                progress=_mode_prog,
                health_factor=hf,
                needs_unstoppable=needs_u and ("unstoppable" in un_allow),
            )
            un_hits = []
            for hit in result.get("hits") or []:
                annotate_win(hit)
                un_hits.append(hit)
            _check(cancel)
            chosen = pick_full_win_lowest_overflow(un_hits)
            if chosen is not None:
                layer = "unpurchasable"
            else:
                # Step 4: purchasable again on the best unpurchasable decks
                # (keeps their rare mods) and send the best effort.
                base_for_last = un_hits or [ev for _sh, _m, ev in boards] or no_item
                last_src = top_by_combined_win(base_for_last, top_n)
                if progress:
                    progress(
                        f"可购买道具再算：综合胜率前 {len(last_src)} 组，取最优可用",
                        92,
                    )
                # Rebuild ships from these decorated solutions.
                last_boards = _seed_boards(last_src, pool, enemies, catalog, owned, hf, boss)
                fallback_hits: list[dict] = []
                for ships_i, mods_i, ev_i in last_boards:
                    _check(cancel)
                    hit = apply_purchasable_best_stat(
                        ships_i,
                        mods_i,
                        ev_i,
                        enemies,
                        catalog,
                        owned,
                        skip_lanes=skip,
                        health_factor=hf,
                        boss_lane=boss,
                    )
                    if hit.get("valid"):
                        fallback_hits.append(hit)
                pool_for_fallback = fallback_hits or last_src
                chosen = pick_best_effort(pool_for_fallback)
                layer = "purchasable_fallback"

    if chosen is None:
        raise ValueError("没有可用阵容")
    chosen = annotate_win(dict(chosen))
    chosen["layer"] = layer
    chosen["note"] = chosen.get("note") or LAYER_ZH.get(layer, layer)
    if attach_effectiveness_lanes:
        chosen = attach_effectiveness(chosen, attach_effectiveness_lanes)
        chosen["layer"] = layer
    if progress:
        progress(f"计算完成 · {LAYER_ZH.get(layer, layer)}", 100)
    return {
        "solution": chosen,
        "layer": layer,
        "full_win": is_full_win(chosen),
        "note": chosen.get("note") or "",
    }


def _power_key(row: KpRow):
    return (float(row.enemy_power), int(row.order), row.id)


def select_target(cache: BuffCache) -> dict | None:
    """Pick the next keypoint.

    Selectable keypoints whose stage destroy quota is still open are fought
    first. Among them, keypoints that remove an enemy buff or our debuff come
    before the lowest enemy board power (``enemy_power`` from the buff cache).

    Once a stage quota is met, leftover non-optional keypoints that are still
    the only way forward are transition fights (one lowest-power ship, no
    search). The operation's last keypoint is never cheap-fought when it pays
    progress tokens — that node uses the effectiveness rule instead.
    """
    if cache is None:
        return None
    attackable = [r for r in cache.iter_rows() if r.status == "available"]
    if not attackable:
        return None
    goal_by_stage = {st.index: st.goal for st in cache.stages}

    def goal_met(row: KpRow) -> bool:
        goal = goal_by_stage.get(row.stage_index) or {}
        return bool(goal.get("met"))

    # Harm-cutting nodes stay on the full solver even after the destroy quota:
    # a cheap one-card fight would leave the enemy buff / our debuff in place.
    core = [r for r in attackable if not r.optional] or list(attackable)
    mitigation = [r for r in core if r.cuts_harm]
    transition = False
    reason = ""
    if mitigation:
        chosen = min(mitigation, key=_power_key)
        reason = "优先削减敌方增益/我方减益"
    else:
        normal = [r for r in core if not goal_met(r)]
        if not normal:
            normal = [r for r in attackable if not goal_met(r)]
        if normal:
            chosen = min(normal, key=_power_key)
            reason = "当前可选据点中敌方战力最低"
        else:
            leftover = [r for r in attackable if not r.optional and goal_met(r)]
            progress_last = [r for r in leftover if r.is_last and r.progress_lanes]
            cheap = [r for r in leftover if r not in progress_last]
            if cheap:
                chosen = min(cheap, key=_power_key)
                transition = True
                reason = "阶段目标已达成，过渡战斗少算"
            elif progress_last:
                chosen = min(progress_last, key=_power_key)
                transition = False
                reason = "行动末据点（进度代币，挂效率提升）"
            else:
                return None

    goal = goal_by_stage.get(chosen.stage_index) or {}
    # Last keypoint with progress tokens always gets the full ladder + effectiveness,
    # even if the stage quota is already met.
    if chosen.is_last and chosen.progress_lanes:
        transition = False
        if "效率提升" not in reason:
            reason = (reason + " · 末据点效率提升").strip(" ·")
    return {
        "keypoint_id": chosen.id,
        "name": chosen.name,
        "enemy_power": chosen.enemy_power,
        "bare_power": chosen.bare_power,
        "stage_index": chosen.stage_index,
        "stage_id": chosen.stage_id,
        "reason": reason,
        "transition": transition,
        "is_last": chosen.is_last,
        "in_last_stage": bool(cache.stages) and chosen.stage_index == cache.stages[-1].index,
        "progress_lanes": list(chosen.progress_lanes),
        "progress_rewards": list(chosen.progress_rewards),
        "cuts_harm": chosen.cuts_harm,
        "stage_goal": dict(goal),
    }


CAMPAIGN_OPS = 3


def normalize_lobby_entry(raw: dict) -> dict:
    status = str(raw.get("status") or raw.get("state") or "").strip().lower()
    completed = bool(
        raw.get("completed")
        or raw.get("isCompleted")
        or raw.get("isCleared")
        or status in {"completed", "cleared", "done", "finished"}
    )
    index = raw.get("index", raw.get("order"))
    try:
        index = int(index) if index is not None else None
    except (TypeError, ValueError):
        index = None
    return {
        "id": str(raw.get("id") or raw.get("operationId") or ""),
        "name": str(raw.get("name") or raw.get("title") or ""),
        "status": status or ("completed" if completed else "active"),
        "completed": completed,
        "index": index,
    }


def lobby_progress(
    cache: BuffCache,
    lobby_ops: list[dict] | None,
    *,
    campaign_size: int = CAMPAIGN_OPS,
) -> dict[str, Any]:
    """Three-operation hall progress plus this operation's cleared/total keypoints.

    Counts come from the live operation (so a resumed half-finished operation is
    correct) and from lobby completion flags. When the hall list is missing, the
    denominator stays 3 and ``index`` on the current operation fills how many
    operations are already behind us.
    """
    items = [normalize_lobby_entry(x) for x in (lobby_ops or []) if isinstance(x, dict)]
    items = [x for x in items if x["id"]]
    current_id = cache.operation_id
    current_done = bool(cache.is_complete)
    found = False
    for it in items:
        if it["id"] == current_id:
            it["completed"] = current_done
            found = True
    if current_id and not found:
        items.append({
            "id": current_id,
            "name": cache.operation_name,
            "status": "completed" if current_done else "active",
            "completed": current_done,
            "index": None,
        })

    indexes = [it["index"] for it in items if isinstance(it.get("index"), int)]
    zero_based = bool(indexes) and (0 in indexes or max(indexes) == len(items) - 1)
    padded = len(items) < campaign_size
    total = len(items) if len(items) >= campaign_size else campaign_size

    if items and len([it for it in items if it["id"] != current_id]) >= 1 and not padded:
        done = sum(1 for it in items if it["completed"])
        uncertain = False
    else:
        cur = next((it for it in items if it["id"] == current_id), None)
        idx = cur.get("index") if cur else None
        if isinstance(idx, int):
            pos = idx if zero_based or idx == 0 else idx - 1
            pos = max(0, int(pos))
            done = pos + (1 if current_done else 0)
            uncertain = padded
        else:
            done = sum(1 for it in items if it["completed"])
            uncertain = padded or len(items) < campaign_size
    done = max(0, min(int(done), int(total)))
    return {
        "ops_done": done,
        "ops_total": int(total),
        "uncertain": uncertain,
        "padded": padded,
        "cleared_kp": cache.cleared_count,
        "total_kp": cache.total_count,
        "current_complete": current_done,
        "current_id": current_id,
        "current_name": cache.operation_name,
        "items": items,
    }


def progress_bar_text(lobby: dict) -> str:
    ops = f"行动 {int(lobby.get('ops_done') or 0)}/{int(lobby.get('ops_total') or CAMPAIGN_OPS)}"
    cleared = int(lobby.get("cleared_kp") or 0)
    total = int(lobby.get("total_kp") or 0)
    return f"{ops} · 据点 {cleared}/{total}"


def progress_fraction(lobby: dict) -> float:
    total_ops = max(1, int(lobby.get("ops_total") or CAMPAIGN_OPS))
    done = float(lobby.get("ops_done") or 0)
    kp_total = int(lobby.get("total_kp") or 0)
    kp_cleared = int(lobby.get("cleared_kp") or 0)
    frac_op = (kp_cleared / kp_total) if kp_total else 0.0
    if lobby.get("current_complete"):
        frac_op = 1.0
    return max(0.0, min(1.0, (done + (0.0 if lobby.get("current_complete") else frac_op)) / total_ops))


def finalize_reason(*, cancelled: bool, completed: bool) -> str:
    if completed and not cancelled:
        return "完成"
    if cancelled:
        return "用户终止"
    return "中断"


def format_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def next_operation_id(lobby: dict, current_id: str) -> str | None:
    items = list(lobby.get("items") or [])
    # Prefer explicit order, else list order.
    def sort_key(it: dict):
        idx = it.get("index")
        return (0, idx) if isinstance(idx, int) else (1, items.index(it) if it in items else 0)

    ordered = sorted(items, key=sort_key)
    seen_current = current_id not in {it.get("id") for it in ordered}
    passed = seen_current
    for it in ordered:
        if it.get("id") == current_id:
            passed = True
            continue
        if passed and not it.get("completed") and it.get("id"):
            return str(it["id"])
    for it in ordered:
        if it.get("id") and it.get("id") != current_id and not it.get("completed"):
            return str(it["id"])
    return None


def find_keypoint(op: dict, kp_id: str) -> dict | None:
    op = unwrap_operation(op)
    from .buff_cache import keypoints_of, stages_of

    for st in stages_of(op):
        for kp in keypoints_of(st):
            if str(kp.get("id") or "") == kp_id:
                return kp
    return None
