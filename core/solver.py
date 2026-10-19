#!/usr/bin/env python3
"""Pacific Front deployment exhaustive solver (no modifiers first).

Certain win when my_power_min > enemy_power_after_debuffs.
Power band is base_sum ± 10 after neighbor buffs.
Debuff -20% applies only if type matches an enemy weakness and direction faces that lane.
Buff +30% of one stat applies to left/right neighbor only.
"""
from __future__ import annotations

import argparse
import math
import json
import itertools
from dataclasses import dataclass, field
from typing import Iterable

DIR_DEBUFF = {
    "forward": 0,
    "forwardLeft": -1,
    "forwardRight": +1,
    "up": 0,
    "left": -1,
    "right": +1,
}
TIP_DIR = {
    "上": "forward",
    "正前": "forward",
    "左": "forwardLeft",
    "右": "forwardRight",
    "左前": "forwardLeft",
    "右前": "forwardRight",
}
TIP_BUFF_DIR = {"左": "left", "右": "right"}
STAT_KEYS = {
    "firepower": "firepower",
    "survivability": "survivability",
    "mobility": "mobility",
    "evasion": "mobility",
    "火力": "firepower",
    "存活性": "survivability",
    "机动性": "mobility",
}


@dataclass
class Effect:
    kind: str  # buff | debuff
    direction: str
    effect_type: str
    value: float = 0.2


@dataclass
class Ship:
    name: str
    firepower: float
    survivability: float
    mobility: float
    hp: int = 3
    effects: list[Effect] = field(default_factory=list)
    preserve_priority: float | None = None
    ship_type: str | None = None
    banned: bool = False
    code: str | None = None

    @property
    def base_sum(self) -> float:
        return self.firepower + self.survivability + self.mobility

    @property
    def cost(self) -> float:
        pri = self.preserve_priority if self.preserve_priority is not None else self.base_sum
        return pri


@dataclass
class Enemy:
    name: str
    power: float
    weaknesses: list[str]
    reward: float = 0
    code: str | None = None


def band(sum_stats: float) -> tuple[float, float]:
    return sum_stats - 10, sum_stats + 10


def apply_buffs(
    ships: list[Ship | None],
    *,
    disable_buffs: bool = False,
    buff_multiplier: float = 1.0,
) -> list[tuple[float, float, float] | None]:
    """Return effective (fp, surv, mob) per slot after neighbor buffs.

    When disable_buffs is True or buff_multiplier == 0 (e.g. 增益效果 -100%),
    neighbor +stat buffs do not apply.
    """
    n = len(ships)
    stats = [
        (None if s is None else (s.firepower, s.survivability, s.mobility))
        for s in ships
    ]
    out = [None if t is None else list(t) for t in stats]
    if disable_buffs or buff_multiplier == 0:
        return [tuple(x) if x is not None else None for x in out]
    for i, s in enumerate(ships):
        if s is None:
            continue
        for e in s.effects:
            if e.kind != "buff":
                continue
            if e.direction == "left":
                j = i - 1
            elif e.direction == "right":
                j = i + 1
            else:
                continue
            if j < 0 or j >= n or ships[j] is None:
                continue
            stat = STAT_KEYS.get(e.effect_type, e.effect_type)
            idx = {"firepower": 0, "survivability": 1, "mobility": 2}.get(stat)
            if idx is None:
                continue
            base = stats[j][idx]
            pct = (e.value if e.value else 0.3) * buff_multiplier
            out[j][idx] = base + base * pct
    return [tuple(x) if x is not None else None for x in out]


def enemy_after_debuffs(enemies: list[Enemy], ships: list[Ship | None]) -> list[float]:
    powers = [e.power for e in enemies]
    n = len(enemies)
    for i, s in enumerate(ships):
        if s is None:
            continue
        for e in s.effects:
            if e.kind != "debuff":
                continue
            delta = DIR_DEBUFF.get(e.direction)
            if delta is None:
                delta = DIR_DEBUFF.get(TIP_DIR.get(e.direction, ""), None)
            if delta is None:
                continue
            j = i + delta
            if j < 0 or j >= n:
                continue
            wset = set(enemies[j].weaknesses)
            et = e.effect_type
            aliases = {
                "largeCaliber": {"largeCaliber", "大口径"},
                "strikeAircraft": {"strikeAircraft", "攻击机", "舰载机突击"},
                "detection": {"detection", "被侦察性", "侦察"},
                "quickFire": {"quickFire", "速射"},
                "smokes": {"smokes", "烟幕"},
                "antiAircraftFire": {"antiAircraftFire", "防空火力"},
            }
            matched = False
            for key, names in aliases.items():
                if et in names or et == key:
                    if wset & names or key in wset or any(x in wset for x in names):
                        matched = True
                        break
            if not matched:
                if et not in wset:
                    continue
            pct = e.value if e.value else 0.2
            powers[j] = powers[j] * (1.0 - pct)
    return powers


def evaluate(
    ships: list[Ship | None],
    enemies: list[Enemy],
    *,
    disable_buffs: bool = False,
    buff_multiplier: float = 1.0,
    require_filled: bool = True,
) -> dict:
    assert len(ships) == len(enemies)
    eff = apply_buffs(ships, disable_buffs=disable_buffs, buff_multiplier=buff_multiplier)
    ep = enemy_after_debuffs(enemies, ships)
    slots = []
    all_certain = True
    total_overflow = 0.0
    filled = 0
    for i, e in enumerate(enemies):
        if ships[i] is None:
            slots.append({"slot": i, "status": "empty", "overflow": None})
            if require_filled:
                all_certain = False
            continue
        filled += 1
        fp, surv, mob = eff[i]
        total = fp + surv + mob
        lo, hi = band(total)
        enemy_p = ep[i]
        overflow = lo - enemy_p
        total_overflow += overflow
        if enemy_p < lo:
            status = "certain"
        elif enemy_p > hi:
            status = "impossible"
            all_certain = False
        else:
            status = "band"
            all_certain = False
        slots.append({
            "slot": i,
            "ship": ships[i].name,
            "ship_code": ships[i].code,
            "eff_stats": [round(fp, 2), round(surv, 2), round(mob, 2)],
            "power_band": [lo, hi],
            "enemy": e.name,
            "enemy_code": getattr(e, "code", None),
            "enemy_power": round(enemy_p, 2),
            "overflow": round(overflow, 2),
            "status": status,
        })
    if filled == 0:
        all_certain = False
    cost = sum(s.cost for s in ships if s is not None)
    reward = sum(e.reward for e, s in zip(enemies, ships) if s is not None)
    return {
        "all_certain": all_certain,
        "cost": cost,
        "reward": reward,
        "efficiency": (reward / cost) if cost else 0,
        "total_overflow": round(total_overflow, 2),
        "filled": filled,
        "slots": slots,
        "ships": [s.name for s in ships if s],
        "ship_codes": [s.code for s in ships if s],
    }


def filter_pool(
    pool: list[Ship],
    *,
    exclude_banned: bool = True,
    exclude_types: Iterable[str] | None = None,
) -> list[Ship]:
    banned_types = {t.lower() for t in (exclude_types or [])}
    out = []
    for s in pool:
        if exclude_banned and s.banned:
            continue
        st = (s.ship_type or "").lower()
        if st and st in banned_types:
            continue
        out.append(s)
    return out


def lane_win_pct(lo: float, hi: float, enemy: float) -> float:
    """P(power > enemy) for discrete integer powers in [ceil(lo), floor(hi)] inclusive.

    If that range is empty, fall back to single-point compare using lo.
    Win condition is strict: my_power > enemy_power.
    """
    try:
        lo, hi, enemy = float(lo), float(hi), float(enemy)
    except Exception:
        return 0.0
    start = math.ceil(lo - 1e-9)
    end = math.floor(hi + 1e-9)
    if end < start:
        return 100.0 if enemy < lo else 0.0
    total = end - start + 1
    wins = sum(1 for p in range(start, end + 1) if p > enemy)
    return wins / total * 100.0


def _eval_win_rates(ev: dict) -> tuple[float, float]:
    """综合 = 四路胜率加总/4；最低 = 已放牌各路中的最小胜率。空位按 0% 计入综合。"""
    slots = list(ev.get("slots") or [])
    while len(slots) < 4:
        slots.append({"status": "empty"})
    four: list[float] = []
    filled: list[float] = []
    for slot in slots[:4]:
        if slot.get("status") == "empty":
            four.append(0.0)
            continue
        band = slot.get("power_band") or [0, 0]
        lo = float(band[0]) if len(band) > 0 else 0.0
        hi = float(band[1]) if len(band) > 1 else lo
        enemy = float(slot.get("enemy_power") or 0)
        pct = lane_win_pct(lo, hi, enemy)
        four.append(pct)
        filled.append(pct)
    avg = sum(four) / 4.0
    mn = min(filled) if filled else 0.0
    return avg, mn


def solve(
    pool: list[Ship],
    enemies: list[Enemy],
    *,
    fill_all: bool = True,
    max_ships: int | None = None,
    min_ships: int | None = None,
    exclude_banned: bool = True,
    exclude_types: Iterable[str] | None = None,
    disable_buffs: bool = False,
    buff_multiplier: float = 1.0,
    progress=None,
    top: int | None = None,
) -> list[dict]:
    """Exhaust placements. Prefer all-certain, then fill with best win-rate boards.

    When no (or not enough) 100% certain boards exist, keep the best partial
    boards by combined win rate so callers can fill the prestore count.

    fill_all=True: place exactly n ships (one per lane).
    fill_all=False: try k = min_ships..max_ships (default 2..n), choosing which
    lanes to fill; empty lanes are allowed and do not break certainty.

    progress(done, total, msg) optional — called periodically during enumeration.
    top: optional cap hint for how many partials to retain (default 200).
    """
    import heapq
    import math
    import time as _time

    pool = filter_pool(pool, exclude_banned=exclude_banned, exclude_types=exclude_types)
    n = len(enemies)
    results = []
    # Min-heap of (avg_win, min_win, overflow, seq, ev) — keep highest win-rate partials
    partial_keep = max(200, int(top or 0) * 5) if top else 200
    partial_heap: list = []
    _seq = 0

    if fill_all:
        ks = [n]
    else:
        hi = max_ships if max_ships is not None else n
        lo = min_ships if min_ships is not None else 2
        ks = list(range(lo, hi + 1))

    def consider(ships: list[Ship | None]):
        nonlocal _seq
        ev = evaluate(
            ships,
            enemies,
            disable_buffs=disable_buffs,
            buff_multiplier=buff_multiplier,
            require_filled=fill_all,
        )
        if ev["all_certain"]:
            results.append(ev)
            return
        avg, mn = _eval_win_rates(ev)
        ev["win_rate_avg"] = round(avg, 1)
        ev["win_rate_min"] = round(mn, 1)
        _seq += 1
        item = (avg, mn, float(ev.get("total_overflow") or 0), _seq, ev)
        if len(partial_heap) < partial_keep:
            heapq.heappush(partial_heap, item)
        else:
            # Replace worst if this is better
            if item[:3] > partial_heap[0][:3]:
                heapq.heapreplace(partial_heap, item)

    def _n_perms(p: int, k: int) -> int:
        if k > p or k < 0:
            return 0
        return math.perm(p, k)

    # Rough total work units (perms)
    total_work = 0
    for k in ks:
        if k > len(pool) or k > n:
            continue
        if k == n:
            total_work += _n_perms(len(pool), k)
        else:
            total_work += math.comb(n, k) * _n_perms(len(pool), k)
    total_work = max(total_work, 1)

    done = 0
    t0 = _time.perf_counter()
    last_report = 0.0
    report_every = max(500, total_work // 200)

    def _maybe_report(force: bool = False):
        nonlocal last_report
        now = _time.perf_counter()
        if not progress:
            return
        if not force and (done - last_report) < report_every and (now - t0) < 0.35:
            return
        elapsed = now - t0
        rate = done / elapsed if elapsed > 0.05 else 0.0
        pct = min(99, int(100 * done / total_work))
        remain = max(0, total_work - done)
        eta = (remain / rate) if rate > 0 else -1
        eta_txt = f" · 预计剩余 ~{eta:.0f}s" if eta >= 0 and pct < 100 else ""
        progress(
            done,
            total_work,
            f"无道具穷举 {done}/{total_work} · {pct}% · {rate:.0f}/s · 已用 {elapsed:.1f}s{eta_txt} · 全确 {len(results)} · 候选 {len(partial_heap)}",
        )
        last_report = done

    for k in ks:
        if k > len(pool) or k > n:
            continue
        if k == n:
            for combo in itertools.combinations(pool, k):
                for perm in itertools.permutations(combo, k):
                    consider(list(perm))
                    done += 1
                    if done % report_every == 0:
                        _maybe_report()
        else:
            for lanes in itertools.combinations(range(n), k):
                for combo in itertools.combinations(pool, k):
                    for perm in itertools.permutations(combo, k):
                        ships: list[Ship | None] = [None] * n
                        for lane, ship in zip(lanes, perm):
                            ships[lane] = ship
                        consider(ships)
                        done += 1
                        if done % report_every == 0:
                            _maybe_report()
    _maybe_report(force=True)
    results.sort(key=lambda r: (-r.get("filled", 0), r["cost"], -r["reward"], -r["total_overflow"]))
    # Fill with best partials by combined win rate when certain boards are scarce
    need = int(top) if top and top > 0 else partial_keep
    if len(results) < need and partial_heap:
        partials = [it[-1] for it in partial_heap]
        partials.sort(
            key=lambda r: (
                -float(r.get("win_rate_avg") or 0),
                -float(r.get("win_rate_min") or 0),
                -float(r.get("total_overflow") or 0),
                r.get("cost", 10**9),
            )
        )
        have = {(tuple(r.get("ship_codes") or r.get("ships") or []),) for r in results}
        for p in partials:
            if len(results) >= need:
                break
            key = (tuple(p.get("ship_codes") or p.get("ships") or []),)
            if key in have:
                continue
            have.add(key)
            results.append(p)
    return results


def load_scenario(path: str) -> tuple[list[Ship], list[Enemy], dict]:
    data = json.load(open(path, encoding="utf-8"))
    return parse_scenario(data)


def parse_scenario(data: dict) -> tuple[list[Ship], list[Enemy], dict]:
    meta = data.get("meta") or {}
    ships = []
    for s in data["ships"]:
        effects = []
        for e in s.get("effects", []):
            effects.append(Effect(
                kind=e["kind"],
                direction=e["direction"],
                effect_type=e.get("type") or e.get("effect_type"),
                value=e.get("value", 0.3 if e["kind"] == "buff" else 0.2),
            ))
        ships.append(Ship(
            name=s["name"],
            firepower=s["firepower"],
            survivability=s["survivability"],
            mobility=s["mobility"],
            hp=s.get("hp", 3),
            effects=effects,
            preserve_priority=s.get("preserve_priority"),
            ship_type=s.get("type") or s.get("ship_type"),
            banned=bool(s.get("banned", False)),
            code=s.get("code"),
        ))
    enemies = [
        Enemy(
            name=e["name"],
            power=e["power"],
            weaknesses=e["weaknesses"],
            reward=e.get("reward", 0),
            code=e.get("code"),
        )
        for e in data["enemies"]
    ]
    if "banned_types" in data:
        meta = {**meta, "banned_types": data["banned_types"]}
    if "exclude_types" in data:
        meta = {**meta, "banned_types": data["exclude_types"]}
    if "disable_buffs" in data:
        meta = {**meta, "disable_buffs": bool(data["disable_buffs"])}
    if "buff_multiplier" in data:
        meta = {**meta, "buff_multiplier": float(data["buff_multiplier"])}
    return ships, enemies, meta


def format_solution(sol: dict, index: int | None = None) -> str:
    head = f"cost={sol['cost']:.1f} reward={sol['reward']} total_overflow={sol['total_overflow']} ships={sol['ships']}"
    if index is not None:
        head = f"#{index} " + head
    lines = [head]
    for slot in sol["slots"]:
        if slot.get("status") == "empty":
            lines.append(f"  lane{slot['slot']+1}: (empty)")
            continue
        lines.append(
            f"  lane{slot['slot']+1}: {slot['ship']} band={slot['power_band']} "
            f"vs {slot['enemy']} {slot['enemy_power']} overflow={slot['overflow']:+.1f} -> {slot['status']}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Exhaustive Pacific Front deployment solver")
    ap.add_argument("scenario", help="JSON scenario path")
    ap.add_argument("-n", "--top", type=int, default=10, help="show top N solutions")
    ap.add_argument("--allow-partial", action="store_true", help="allow fewer ships than enemies (try 2..n lanes)")
    ap.add_argument("--min-ships", type=int, default=None, help="with --allow-partial, minimum ships to place (default 2)")
    ap.add_argument("--max-ships", type=int, default=None, help="with --allow-partial, maximum ships to place (default n)")
    ap.add_argument("--include-banned", action="store_true", help="do not drop banned ships")
    ap.add_argument(
        "--exclude-type",
        action="append",
        default=[],
        help="exclude ship type (repeatable), e.g. cruiser",
    )
    ap.add_argument("--json-out", help="write full top solutions JSON to this path")
    args = ap.parse_args()
    ships, enemies, meta = load_scenario(args.scenario)
    exclude_types = list(args.exclude_type) or list(meta.get("banned_types") or [])
    # Auto-exclude cruiser if any ship marked banned with type cruiser and no explicit types
    if not exclude_types and any(s.banned and (s.ship_type or "").lower() == "cruiser" for s in ships):
        exclude_types = ["cruiser"]
    disable_buffs = bool(meta.get("disable_buffs", False))
    buff_multiplier = float(meta.get("buff_multiplier", 0.0 if disable_buffs else 1.0))
    sols = solve(
        ships,
        enemies,
        fill_all=not args.allow_partial,
        min_ships=args.min_ships,
        max_ships=args.max_ships,
        exclude_banned=not args.include_banned,
        exclude_types=exclude_types,
        disable_buffs=disable_buffs,
        buff_multiplier=buff_multiplier,
    )
    pool_n = len(filter_pool(ships, exclude_banned=not args.include_banned, exclude_types=exclude_types))
    print(f"pool={pool_n} certain solutions: {len(sols)} disable_buffs={disable_buffs} buff_mult={buff_multiplier}")
    for i, s in enumerate(sols[: args.top], 1):
        print()
        print(format_solution(s, i))
    if args.json_out:
        payload = {
            "pool": pool_n,
            "certain_count": len(sols),
            "exclude_types": exclude_types,
            "solutions": sols[: args.top],
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
