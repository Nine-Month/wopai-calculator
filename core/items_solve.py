#!/usr/bin/env python3
"""Pacific Front item-layer exhaustive solver (purchasable / unpurchasable / both).

Assumptions (effect model from frontend modifier JS + locale; settings API has id/type/price only):
  - Slot rule: at most 1 common + 1 special per ship.
  - Stat training (+20% fp/surv/mob) multiplies health-scaled specs before neighbor buffs.
  - Focused: +40% all specs; disables that unit's outgoing buffs/debuffs and incoming buffs.
  - Teamwork: x2 neighbor buff power from this unit.
  - Specialization: x2 debuff power from this unit.
  - Sea Dog: debuff ignores weakness match.
  - Fast-repair: +2 HP (cap 3); rescale combat stats by HealthFactor; occupies special slot.
  - Unstoppable: ignore battle start conditions; no combat-stat effect; occupies special slot.
  - Debuffs stack multiplicatively when multiple hit the same enemy.
  - Certain win iff my_power_min (sum-10) > enemy_power_after_debuffs.
  - supply_spent = price x max(0, used - owned); inventory use costs 0.
  - Search uses combat-affecting mods (incl. repair/unstoppable when user-selected).
  - Non-combat: effectiveness / moral-impact — never searched.
  - Search seeds: no-item boards that already certain-kill the hardest (boss) lane,
    then exhaust combat mod boards on failing (+ support) lanes.
  - Search prune (purchasable/both): among gunners/DC/rudder, a ship only tries the
    training matching its highest health-scaled base stat.
  - Fast-repair is not offered on ships already at 3 HP.
"""
from __future__ import annotations

import argparse
import itertools
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .solver import (
    DIR_DEBUFF,
    STAT_KEYS,
    TIP_DIR,
    Ship,
    band,
    filter_pool,
    parse_scenario,
)

ROOT = Path(__file__).resolve().parent.parent

# Combat pools (user filter ∩ these). Non-combat: effectiveness, moral-impact.
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
# Single-stat trainings: only the one matching a ship's highest base stat is considered.
STAT_TRAINING = {
    "firepower": "gunners-training",
    "survivability": "damage-control-training",
    "mobility": "rudder-crew-training",
}
STAT_TRAINING_IDS = set(STAT_TRAINING.values())

DEFAULT_HEALTH_FACTOR = [0.8, 0.9, 1.0]

EFFECTS = {
    "gunners-training": dict(kind="stat", stat="firepower", pct=0.20, note="+20% firepower"),
    "damage-control-training": dict(kind="stat", stat="survivability", pct=0.20, note="+20% survivability"),
    "rudder-crew-training": dict(kind="stat", stat="mobility", pct=0.20, note="+20% mobility"),
    "teamwork": dict(kind="teamwork", mult=2.0, note="buff power x2"),
    "effectiveness": dict(kind="tokens", note="+10% Progress Tokens (non-combat)"),
    "moral-impact": dict(kind="moral", note="kill counts as 2 toward stage goal (non-combat search)"),
    "focused": dict(kind="focused", all_pct=0.40, note="+40% all; disable buffs/debuffs"),
    "specialization": dict(kind="specialization", mult=2.0, note="debuff power x2"),
    "sea-dog": dict(kind="sea_dog", note="debuff ignores weakness"),
    "fast-repair": dict(kind="repair", note="+2 HP (cap 3); rescale by HealthFactor"),
    "unstoppable": dict(kind="unstoppable", note="ignore battle conditions"),
}

WEAKNESS_ALIASES = {
    "largeCaliber": {"largeCaliber", "大口径"},
    "strikeAircraft": {"strikeAircraft", "攻击机", "舰载机突击"},
    "detection": {"detection", "被侦察性", "侦察"},
    "quickFire": {"quickFire", "速射"},
    "smokes": {"smokes", "烟幕"},
    "antiAircraftFire": {"antiAircraftFire", "防空火力"},
}


@dataclass(frozen=True)
class ModDef:
    id: str
    group: str  # common | special
    price: int | None
    kind: str
    stat: str | None = None
    pct: float = 0.0
    all_pct: float = 0.0
    mult: float = 1.0
    note: str = ""

    @property
    def purchasable(self) -> bool:
        return self.price is not None


@dataclass
class LaneMods:
    common: str | None = None
    special: str | None = None

    def ids(self) -> list[str]:
        out: list[str] = []
        if self.common:
            out.append(self.common)
        if self.special:
            out.append(self.special)
        return out


def _unwrap(data: dict) -> dict:
    return data["result"] if isinstance(data, dict) and "result" in data else data


def resolve_dump(explicit: Path | None, scenario: Path, kind: str) -> Path | None:
    if explicit is not None:
        return explicit
    stem = scenario.stem
    candidates = [
        ROOT / "dumps" / f"{stem}_{kind}.json",
        ROOT / "dumps" / f"{kind}.json",
        scenario.parent.parent / "dumps" / f"{stem}_{kind}.json",
        scenario.parent.parent / "dumps" / f"{kind}.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_catalog(settings_path: Path, inventory_path: Path | None):
    settings = _unwrap(json.loads(settings_path.read_text(encoding="utf-8")))
    owned: dict[str, int] = {}
    eventum = 0
    if inventory_path and inventory_path.is_file():
        inv = _unwrap(json.loads(inventory_path.read_text(encoding="utf-8")))
        owned = {m["id"]: int(m.get("amount") or 0) for m in inv.get("modifiers") or []}
        eventum = int((inv.get("currencies") or {}).get("eventum_1") or 0)

    catalog: dict[str, ModDef] = {}
    for m in settings.get("collection", {}).get("modifiers") or []:
        mid = m["id"]
        price = None
        if m.get("price"):
            price = int(m["price"][0]["amount"])
        group = "common" if m.get("type") == 1 else "special"
        eff = EFFECTS.get(mid, dict(kind="noop", note="unknown"))
        catalog[mid] = ModDef(
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
    for mid, eff in EFFECTS.items():
        if mid not in catalog:
            group = "common" if mid in PURCHASABLE_COMBAT else "special"
            catalog[mid] = ModDef(
                id=mid,
                group=group,
                price=12 if group == "common" else None,
                kind=eff["kind"],
                stat=eff.get("stat"),
                pct=float(eff.get("pct") or 0),
                all_pct=float(eff.get("all_pct") or 0),
                mult=float(eff.get("mult") or 1),
                note=eff.get("note") or "",
            )
    return catalog, owned, eventum


def health_factor_from_meta(meta: dict | None, scenario: dict | None = None) -> list[float]:
    """Resolve [HP1, HP2, HP3] multipliers from scenario/meta."""
    for src in (meta or {}, scenario or {}):
        hf = src.get("health_factor")
        if isinstance(hf, list) and len(hf) >= 3:
            return [float(hf[0]), float(hf[1]), float(hf[2])]
        nested = src.get("meta") if isinstance(src.get("meta"), dict) else None
        if nested:
            hf = nested.get("health_factor")
            if isinstance(hf, list) and len(hf) >= 3:
                return [float(hf[0]), float(hf[1]), float(hf[2])]
    return list(DEFAULT_HEALTH_FACTOR)


def apply_repair_to_ship(ship: Ship, health_factor: list[float], amount: int = 2) -> Ship:
    """Return a copy with HP += amount (cap 3) and combat stats rescaled by HealthFactor."""
    old_hp = int(ship.hp or 3)
    new_hp = min(3, old_hp + int(amount))
    if new_hp == old_hp:
        return ship
    hf = health_factor if health_factor and len(health_factor) >= 3 else DEFAULT_HEALTH_FACTOR
    old_f = float(hf[max(1, min(3, old_hp)) - 1])
    new_f = float(hf[max(1, min(3, new_hp)) - 1])
    ratio = (new_f / old_f) if old_f > 1e-9 else 1.0
    return Ship(
        name=ship.name,
        firepower=float(ship.firepower) * ratio,
        survivability=float(ship.survivability) * ratio,
        mobility=float(ship.mobility) * ratio,
        hp=new_hp,
        effects=ship.effects,
        preserve_priority=ship.preserve_priority,
        ship_type=ship.ship_type,
        banned=ship.banned,
        code=ship.code,
    )


def ships_after_repairs(
    ships: list[Ship],
    lane_mods: list[LaneMods],
    catalog: dict[str, ModDef],
    health_factor: list[float],
) -> tuple[list[Ship], list[dict]]:
    """Apply fast-repair on lanes that carry it. Returns (ships, repair records)."""
    out = list(ships)
    repairs: list[dict] = []
    for i, lm in enumerate(lane_mods):
        if i >= len(ships):
            break
        has_repair = False
        for mid in lm.ids():
            d = catalog.get(mid)
            if d is not None and d.kind == "repair":
                has_repair = True
                break
        if not has_repair:
            continue
        old = ships[i]
        new = apply_repair_to_ship(old, health_factor, 2)
        out[i] = new
        repairs.append({
            "lane": i,
            "ship": old.name,
            "ship_code": old.code,
            "from_hp": int(old.hp or 3),
            "to_hp": int(new.hp or 3),
            "mod": "fast-repair",
        })
    return out, repairs


def apply_stat_mods(ship, mods: LaneMods, catalog: dict[str, ModDef]):
    fp, surv, mob = ship.firepower, ship.survivability, ship.mobility
    focused = False
    for mid in mods.ids():
        d = catalog[mid]
        if d.kind == "focused":
            focused = True
            fp *= 1.0 + d.all_pct
            surv *= 1.0 + d.all_pct
            mob *= 1.0 + d.all_pct
        elif d.kind == "stat":
            if d.stat == "firepower":
                fp *= 1.0 + d.pct
            elif d.stat == "survivability":
                surv *= 1.0 + d.pct
            elif d.stat == "mobility":
                mob *= 1.0 + d.pct
    return fp, surv, mob, focused


def apply_buffs_modded(ships, lane_mods, catalog):
    n = len(ships)
    base_stats, focused_flags = [], []
    for i, s in enumerate(ships):
        fp, surv, mob, focused = apply_stat_mods(s, lane_mods[i], catalog)
        base_stats.append((fp, surv, mob))
        focused_flags.append(focused)
    out = [list(t) for t in base_stats]
    for i, s in enumerate(ships):
        if focused_flags[i]:
            continue
        bmult = 1.0
        for mid in lane_mods[i].ids():
            if catalog[mid].kind == "teamwork":
                bmult *= catalog[mid].mult
        for e in s.effects:
            if e.kind != "buff":
                continue
            j = i - 1 if e.direction == "left" else (i + 1 if e.direction == "right" else None)
            if j is None or not (0 <= j < n) or focused_flags[j]:
                continue
            idx = {"firepower": 0, "survivability": 1, "mobility": 2}.get(
                STAT_KEYS.get(e.effect_type, e.effect_type)
            )
            if idx is None:
                continue
            base = base_stats[j][idx]
            out[j][idx] = base + base * ((e.value or 0.3) * bmult)
    return [tuple(x) for x in out]


def enemy_after_debuffs_modded(enemies, ships, lane_mods, catalog):
    powers = [e.power for e in enemies]
    n = len(enemies)
    for i, s in enumerate(ships):
        if any(catalog[mid].kind == "focused" for mid in lane_mods[i].ids()):
            continue
        dmult, sea = 1.0, False
        for mid in lane_mods[i].ids():
            d = catalog[mid]
            if d.kind == "specialization":
                dmult *= d.mult
            if d.kind == "sea_dog":
                sea = True
        for e in s.effects:
            if e.kind != "debuff":
                continue
            delta = DIR_DEBUFF.get(e.direction)
            if delta is None:
                delta = DIR_DEBUFF.get(TIP_DIR.get(e.direction, ""), None)
            if delta is None:
                continue
            j = i + delta
            if not (0 <= j < n):
                continue
            wset = set(enemies[j].weaknesses)
            et = e.effect_type
            matched = sea
            if not matched:
                for key, names in WEAKNESS_ALIASES.items():
                    if et in names or et == key:
                        if wset & names or key in wset or any(x in wset for x in names):
                            matched = True
                            break
                if not matched and et in wset:
                    matched = True
            if matched:
                powers[j] *= 1.0 - ((e.value or 0.2) * dmult)
    return powers


def purchase_and_used(lane_mods, catalog, owned):
    used: dict[str, int] = {}
    for lm in lane_mods:
        for mid in lm.ids():
            used[mid] = used.get(mid, 0) + 1
    spent, purchases = 0, []
    for mid, need in used.items():
        buy = max(0, need - owned.get(mid, 0))
        if buy:
            if catalog[mid].price is None:
                return None, used, []
            spent += buy * catalog[mid].price
            purchases.append({"id": mid, "buy": buy, "unit_price": catalog[mid].price})
    return spent, used, purchases


def evaluate_with_mods(
    ships,
    enemies,
    lane_mods,
    catalog,
    owned,
    boss_lane: int,
    *,
    health_factor: list[float] | None = None,
):
    hf = health_factor if health_factor and len(health_factor) >= 3 else DEFAULT_HEALTH_FACTOR
    ships_eff, repairs = ships_after_repairs(ships, lane_mods, catalog, hf)
    eff = apply_buffs_modded(ships_eff, lane_mods, catalog)
    ep = enemy_after_debuffs_modded(enemies, ships_eff, lane_mods, catalog)
    slots = []
    all_certain = True
    total_overflow = 0.0
    has_unstoppable = False
    for i, e in enumerate(enemies):
        fp, surv, mob = eff[i]
        lo, hi = band(fp + surv + mob)
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
        mods = lane_mods[i].ids()
        if "unstoppable" in mods:
            has_unstoppable = True
        slot = {
            "slot": i,
            "ship": ships_eff[i].name,
            "ship_code": ships_eff[i].code,
            "eff_stats": [round(fp, 2), round(surv, 2), round(mob, 2)],
            "power_band": [round(lo, 2), round(hi, 2)],
            "enemy": e.name,
            "enemy_code": getattr(e, "code", None),
            "enemy_power": round(enemy_p, 2),
            "overflow": round(overflow, 2),
            "status": status,
            "mods": mods,
            "hp": int(ships_eff[i].hp or 3),
            "hp_before": int(ships[i].hp or 3),
        }
        slots.append(slot)
    pack = purchase_and_used(lane_mods, catalog, owned)
    if pack[0] is None:
        return {"valid": False}
    spent, used, purchases = pack
    n_certain = sum(1 for s in slots if s["status"] == "certain")
    return {
        "valid": True,
        "all_certain": all_certain,
        "cost": sum(s.cost for s in ships_eff),
        "reward": sum(e.reward for e, s in zip(enemies, ships_eff)),
        "total_overflow": round(total_overflow, 2),
        "slots": slots,
        "ships": [s.name for s in ships_eff],
        "ship_codes": [s.code for s in ships_eff],
        "lane_mods": [lm.ids() for lm in lane_mods],
        "supply_spent": spent,
        "purchases": purchases,
        "used_counts": used,
        "certain_lanes": [s["slot"] + 1 for s in slots if s["status"] == "certain"],
        "n_certain": n_certain,
        "boss_certain": slots[boss_lane]["status"] == "certain",
        "overflows": [s["overflow"] for s in slots],
        "repairs": repairs,
        "has_unstoppable": has_unstoppable,
        "can_start": True,  # refined by caller when needs_unstoppable
    }


def empty_mods(n: int = 4) -> list[LaneMods]:
    return [LaneMods() for _ in range(n)]


def mode_mod_lists(
    mode: str,
    *,
    allow_unstoppable: bool = False,
    allowed_mods: list[str] | set[str] | None = None,
):
    """Combat commons/specials for the mode, intersected with optional user whitelist.

    fast-repair / unstoppable live in UNPURCHASABLE_COMBAT; include them when
    the whitelist contains them (or whitelist is None). allow_unstoppable is
    kept for CLI compat: when True and whitelist is None, ensure unstoppable
    is present (already in the pool).
    """
    commons: list[str] = []
    specials: list[str] = []
    if mode in ("purchasable", "both"):
        commons += list(PURCHASABLE_COMBAT)
    if mode in ("unpurchasable", "both"):
        specials += list(UNPURCHASABLE_COMBAT)
    if allowed_mods is not None:
        allow = set(allowed_mods)
        commons = [m for m in commons if m in allow]
        specials = [m for m in specials if m in allow]
    elif not allow_unstoppable:
        # CLI default: drop unstoppable unless explicitly allowed
        specials = [m for m in specials if m != "unstoppable"]
    return commons, specials


def best_stat_training(ship) -> str:
    """Training mod matching ship's highest health-scaled base stat."""
    stats = {
        "firepower": ship.firepower,
        "survivability": ship.survivability,
        "mobility": ship.mobility,
    }
    best = max(stats, key=lambda k: (stats[k], k))
    return STAT_TRAINING[best]


def commons_for_ship(ship, commons: list[str]) -> list[str]:
    """Keep at most one training mod (ship's best-stat match); pass through others."""
    if not commons:
        return []
    best = best_stat_training(ship)
    out: list[str] = []
    for mid in commons:
        if mid in STAT_TRAINING_IDS:
            if mid == best and mid not in out:
                out.append(mid)
        elif mid not in out:
            out.append(mid)
    return out


def specials_for_ship(ship, specials: list[str]) -> list[str]:
    """Drop fast-repair on full-HP ships (no effect, wastes rare slot)."""
    if not specials:
        return []
    hp = int(getattr(ship, "hp", 3) or 3)
    out: list[str] = []
    for mid in specials:
        if mid == "fast-repair" and hp >= 3:
            continue
        if mid not in out:
            out.append(mid)
    return out


def boards_for_fails(
    n_lanes: int,
    fail: list[int],
    ok: list[int],
    ships,
    *,
    commons: list[str],
    specials: list[str],
    max_boards: int | None = None,
):
    """Product on fail lanes + light support. Training mods pruned to best-stat per ship."""
    boards: list[list[LaneMods]] = []

    def add(lm: list[LaneMods]):
        boards.append(lm)

    if not fail:
        add(empty_mods(n_lanes))
        return boards

    per_fail = []
    for lane in fail:
        c_opts = [None] + commons_for_ship(ships[lane], commons)
        s_opts = [None] + specials_for_ship(ships[lane], specials)
        per_fail.append(list(itertools.product(c_opts, s_opts)))

    for prod in itertools.product(*per_fail):
        lm = empty_mods(n_lanes)
        empty = True
        for lane, (c, s) in zip(fail, prod):
            if c or s:
                empty = False
            lm[lane] = LaneMods(common=c, special=s)
        if empty:
            continue
        add(lm)

    # Light support only when few fail lanes: teamwork on any empty-common lane,
    # and one special on any empty-special lane (single addition per base).
    # Skipped when fail>=3 to keep both-mode tractable; fail-lane product is enough.
    if len(fail) <= 2:
        seeded = list(boards)
        for base in seeded:
            for lane in range(n_lanes):
                ship_c = commons_for_ship(ships[lane], commons)
                ship_s = specials_for_ship(ships[lane], specials)
                tw = "teamwork" if "teamwork" in ship_c else None
                if tw and not base[lane].common:
                    lm = [LaneMods(common=x.common, special=x.special) for x in base]
                    lm[lane].common = tw
                    add(lm)
                if not base[lane].special:
                    for s in ship_s:
                        lm = [LaneMods(common=x.common, special=x.special) for x in base]
                        lm[lane].special = s
                        add(lm)

    seen: set[tuple] = set()
    out: list[list[LaneMods]] = []
    for b in boards:
        key = tuple((x.common, x.special) for x in b)
        if key not in seen:
            seen.add(key)
            out.append(b)
            if max_boards is not None and len(out) >= max_boards:
                break
    return out


def rank_key(r: dict):
    return (
        0 if r.get("all_certain") else 1,
        0 if r.get("can_start", True) else 1,
        r.get("supply_spent", 10**9),
        r.get("cost", 10**9),
        -r.get("total_overflow", 0),
    )


def format_sol(ev: dict, idx: int) -> str:
    lines = [
        f"#{idx} supply_spent={ev['supply_spent']} preserve_cost={ev['cost']:.1f} "
        f"n_certain={ev['n_certain']} boss={ev['boss_certain']} all4={ev['all_certain']} "
        f"total_overflow={ev['total_overflow']} ships={ev['ships']}"
    ]
    lines.append(f"  codes={ev['ship_codes']}")
    lines.append(
        f"  mods_by_lane={ev['lane_mods']} purchases={ev['purchases']} used={ev['used_counts']}"
    )
    if ev.get("repairs"):
        lines.append(f"  repairs={ev['repairs']}")
    for s in ev["slots"]:
        lines.append(
            f"  lane{s['slot']+1}: {s['ship']} mods={s['mods']} band={s['power_band']} "
            f"vs {s['enemy']} {s['enemy_power']} overflow={s['overflow']:+.1f} -> {s['status']}"
        )
    lines.append(f"  certain_lanes={ev['certain_lanes']} mode={ev.get('_mode')}")
    return "\n".join(lines)


def one_line_board(ev: dict | None) -> str:
    if not ev:
        return "(none)"
    parts = []
    for s in ev["slots"]:
        mods = "+".join(s["mods"]) if s["mods"] else "-"
        parts.append(f"{s['ship']}[{mods}]({s['overflow']:+.1f})")
    return (
        f"supply={ev['supply_spent']} preserve={ev['cost']:.0f} "
        f"all4={ev['all_certain']} | " + " / ".join(parts)
    )


def pick_boss_lane(enemies, override: int | None) -> int:
    if override is not None:
        return override
    return max(range(len(enemies)), key=lambda i: enemies[i].power)


def collect_boss_seeds(pool, enemies, catalog, owned, boss_lane: int, progress=None,
                       health_factor: list[float] | None = None):
    import math
    import time as _time
    n = len(enemies)
    boss_boards = []
    nperm = 0
    empty = empty_mods(n)
    total = max(1, math.perm(len(pool), n) if len(pool) >= n else 1)
    t0 = _time.perf_counter()
    print(f"phase1: pool={len(pool)} enumerating...", flush=True)
    for combo in itertools.combinations(pool, n):
        for perm in itertools.permutations(combo):
            nperm += 1
            sh = list(perm)
            ev = evaluate_with_mods(
                sh, enemies, empty, catalog, owned, boss_lane, health_factor=health_factor
            )
            if ev.get("boss_certain"):
                boss_boards.append((sh, ev))
            if nperm % 50000 == 0 or (progress and nperm % 2000 == 0):
                print(f"  phase1 perms={nperm} boss_certain={len(boss_boards)}", flush=True)
                if progress:
                    elapsed = _time.perf_counter() - t0
                    rate = nperm / elapsed if elapsed > 0.05 else 0.0
                    # seeds phase maps to 5%..40%
                    pct = 5 + int(35 * min(1.0, nperm / total))
                    remain = max(0, total - nperm)
                    eta = (remain / rate) if rate > 0 else -1
                    eta_txt = f" · 预计剩余 ~{eta:.0f}s" if eta >= 0 else ""
                    progress(
                        pct,
                        f"搜集种子 {nperm}/{total} · {rate:.0f}/s · 已用 {elapsed:.1f}s{eta_txt} · 种子 {len(boss_boards)}",
                    )
    return boss_boards, nperm


def solve_mode(
    mode: str,
    boss_boards,
    enemies,
    catalog,
    owned,
    boss_lane: int,
    *,
    allow_unstoppable: bool = False,
    max_boards_per_fail: int | None = None,
    require_boss: bool = True,
    min_certain: int = 3,
    allowed_mods: list[str] | set[str] | None = None,
    progress=None,
    health_factor: list[float] | None = None,
    needs_unstoppable: bool = False,
):
    # When UI passes allowed_mods, unstoppable inclusion is controlled by the filter.
    # allow_unstoppable still forces inclusion when allowed_mods is None (CLI).
    if allowed_mods is not None and "unstoppable" in set(allowed_mods):
        allow_unstoppable = True
    commons, specials = mode_mod_lists(
        mode, allow_unstoppable=allow_unstoppable, allowed_mods=allowed_mods
    )
    n = len(enemies)
    hits, seen, scanned = [], set(), 0
    board_cache: dict[tuple, list] = {}
    t0 = time.perf_counter()
    n_seeds = max(1, len(boss_boards))
    print(
        f"mode={mode} commons={commons} specials={specials} seeds={len(boss_boards)} "
        f"needs_unstoppable={needs_unstoppable}",
        flush=True,
    )
    for bi, (sh, base) in enumerate(boss_boards):
        fail = tuple(i for i, s in enumerate(base["slots"]) if s["status"] != "certain")
        ok = tuple(i for i, s in enumerate(base["slots"]) if s["status"] == "certain")
        train_sig = tuple(best_stat_training(sh[i]) if commons else None for i in range(n))
        hp_sig = tuple(int(getattr(sh[i], "hp", 3) or 3) for i in range(n))
        cache_key = (fail, train_sig, hp_sig, tuple(specials))
        if cache_key not in board_cache:
            board_cache[cache_key] = boards_for_fails(
                n, list(fail), list(ok), sh,
                commons=commons, specials=specials,
                max_boards=max_boards_per_fail,
            )
        for board in board_cache[cache_key]:
            scanned += 1
            ev = evaluate_with_mods(
                sh, enemies, board, catalog, owned, boss_lane, health_factor=health_factor
            )
            if not ev.get("valid"):
                continue
            if require_boss and not ev.get("boss_certain"):
                continue
            if ev.get("n_certain", 0) < min_certain:
                continue
            # Battle-condition gate: if KP needs unstoppable and user enabled it,
            # only keep boards that actually place it (otherwise still "无法开战").
            if needs_unstoppable and allow_unstoppable:
                if not ev.get("has_unstoppable"):
                    continue
                ev["can_start"] = True
            elif needs_unstoppable and not allow_unstoppable:
                ev["can_start"] = False
            else:
                ev["can_start"] = True
            key = (tuple(ev["ship_codes"]), tuple(tuple(x) for x in ev["lane_mods"]))
            if key in seen:
                continue
            seen.add(key)
            ev["_mode"] = mode
            hits.append(ev)
        if (bi + 1) % 500 == 0 or (progress and (bi + 1) % 50 == 0):
            print(
                f"  mode={mode} seeds={bi+1}/{len(boss_boards)} "
                f"evals={scanned} hits={len(hits)}",
                flush=True,
            )
            if progress:
                elapsed = time.perf_counter() - t0
                # item exhaust maps 40%..95%
                frac = (bi + 1) / n_seeds
                pct = 40 + int(55 * frac)
                rate = scanned / elapsed if elapsed > 0.05 else 0.0
                eta = (elapsed / frac - elapsed) if frac > 0.02 else -1
                eta_txt = f" · 预计剩余 ~{eta:.0f}s" if eta >= 0 else ""
                progress(
                    pct,
                    f"穷举道具 种子{bi+1}/{n_seeds} · 评估 {scanned} · {rate:.0f}/s · 已用 {elapsed:.1f}s{eta_txt} · 命中 {len(hits)}",
                )
    hits.sort(key=rank_key)
    elapsed = time.perf_counter() - t0
    return {
        "mode": mode,
        "hits": hits,
        "scanned": scanned,
        "elapsed": elapsed,
        "four": sum(1 for h in hits if h["all_certain"]),
        "cache_sizes": {str(k): len(v) for k, v in board_cache.items()},
        "commons": commons,
        "specials": specials,
    }


def run_solve(
    scenario_path: Path,
    *,
    modes: Iterable[str],
    settings_path: Path,
    inventory_path: Path | None,
    top: int = 10,
    boss_lane: int | None = None,
    allow_unstoppable: bool = False,
    min_certain: int = 3,
):
    sc = json.loads(scenario_path.read_text(encoding="utf-8"))
    ships, enemies, meta = parse_scenario(sc)
    catalog, owned, eventum = load_catalog(settings_path, inventory_path)
    bl = pick_boss_lane(enemies, boss_lane)
    exclude_types = list(meta.get("banned_types") or [])
    pool = filter_pool(ships, exclude_banned=True, exclude_types=exclude_types)
    hf = health_factor_from_meta(meta, sc)
    needs_u = bool(meta.get("needs_unstoppable") or sc.get("needs_unstoppable"))

    t0 = time.perf_counter()
    boss_boards, nperm = collect_boss_seeds(
        pool, enemies, catalog, owned, bl, health_factor=hf
    )
    phase1_elapsed = time.perf_counter() - t0
    by_nc: dict[int, int] = {}
    for _, ev in boss_boards:
        by_nc[ev["n_certain"]] = by_nc.get(ev["n_certain"], 0) + 1

    results = {}
    for mode in modes:
        cap = 8000 if mode == "both" else None
        results[mode] = solve_mode(
            mode,
            boss_boards,
            enemies,
            catalog,
            owned,
            bl,
            allow_unstoppable=allow_unstoppable,
            max_boards_per_fail=cap,
            min_certain=min_certain,
            health_factor=hf,
            needs_unstoppable=needs_u,
        )

    summary = {
        "scenario": str(scenario_path),
        "settings": str(settings_path),
        "inventory": str(inventory_path) if inventory_path else None,
        "eventum": eventum,
        "owned": owned,
        "pool": len(pool),
        "boss_lane": bl + 1,
        "boss_enemy": enemies[bl].name,
        "boss_power": enemies[bl].power,
        "phase1_perms": nperm,
        "phase1_boss_certain": len(boss_boards),
        "phase1_by_nc": by_nc,
        "phase1_elapsed": round(phase1_elapsed, 2),
        "catalog": {k: asdict(v) for k, v in catalog.items()},
        "needs_unstoppable": needs_u,
        "modes": {},
    }
    for mode, r in results.items():
        best = r["hits"][0] if r["hits"] else None
        best4 = next((h for h in r["hits"] if h["all_certain"]), None)
        summary["modes"][mode] = {
            "hits": len(r["hits"]),
            "four": r["four"],
            "scanned": r["scanned"],
            "elapsed": round(r["elapsed"], 2),
            "commons": r["commons"],
            "specials": r["specials"],
            "cache_sizes": r["cache_sizes"],
            "best": best,
            "best_all_certain": best4,
            "top": r["hits"][:top],
        }
    return summary, results


def write_compare(summary: dict, path: Path):
    lines = []
    lines.append("=== ITEM-LAYER MODE COMPARE ===")
    lines.append(
        f"scenario={summary['scenario']} boss_lane={summary['boss_lane']} "
        f"({summary['boss_enemy']} {summary['boss_power']}) "
        f"pool={summary['pool']} eventum={summary['eventum']}"
    )
    lines.append(
        f"phase1: perms={summary['phase1_perms']} boss_certain={summary['phase1_boss_certain']} "
        f"by_nc={summary['phase1_by_nc']} elapsed={summary['phase1_elapsed']}s"
    )
    lines.append("combat search incl. repair/unstoppable when selected; training pruned to best-stat")
    lines.append("")
    for mode, m in summary["modes"].items():
        lines.append(
            f"MODE={mode} hits={m['hits']} four={m['four']} "
            f"evals={m['scanned']} elapsed={m['elapsed']}s"
        )
        lines.append(f"  commons={m['commons']}")
        lines.append(f"  specials={m['specials']}")
        best = m.get("best_all_certain") or m.get("best")
        if best:
            lines.append(f"  BEST: {one_line_board(best)}")
            lines.append(format_sol(best, 1))
        else:
            lines.append("  BEST: (none)")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="Pacific Front item-layer exhaustive solve "
        "(purchasable / unpurchasable / both). Combat mods + optional repair/unstoppable.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("scenario", help="scenario JSON (ships/enemies)")
    ap.add_argument(
        "--mode",
        default="all",
        choices=["purchasable", "unpurchasable", "both", "all"],
        help="item pool: purchasable | unpurchasable | both | all",
    )
    ap.add_argument("--settings", type=Path, default=None, help="settings dump")
    ap.add_argument("--inventory", type=Path, default=None, help="inventory dump")
    ap.add_argument("-n", "--top", type=int, default=10, help="top N per mode in JSON")
    ap.add_argument("--json-out", type=Path, default=None, help="write full results JSON")
    ap.add_argument("--compare-out", type=Path, default=None, help="write mode compare text")
    ap.add_argument("--boss-lane", type=int, default=None, help="1-based boss lane")
    ap.add_argument("--allow-unstoppable", action="store_true", help="include unstoppable")
    ap.add_argument("--min-certain", type=int, default=3, help="min certain lanes to keep")
    args = ap.parse_args()

    scenario = Path(args.scenario)
    if not scenario.is_file():
        scenario = ROOT / args.scenario
    settings = resolve_dump(args.settings, scenario, "settings")
    inventory = resolve_dump(args.inventory, scenario, "inventory")
    if settings is None:
        raise SystemExit(f"settings dump not found for {scenario}; pass --settings")

    modes = ["purchasable", "unpurchasable", "both"] if args.mode == "all" else [args.mode]
    boss_lane = (args.boss_lane - 1) if args.boss_lane else None
    summary, _results = run_solve(
        scenario,
        modes=modes,
        settings_path=settings,
        inventory_path=inventory,
        top=args.top,
        boss_lane=boss_lane,
        allow_unstoppable=args.allow_unstoppable,
        min_certain=args.min_certain,
    )

    compare_path = args.compare_out
    if compare_path is None and args.mode == "all":
        compare_path = ROOT / "out" / f"{scenario.stem}_modes_compare.txt"
    if compare_path is not None:
        write_compare(summary, compare_path)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"pool={summary['pool']} boss_lane={summary['boss_lane']} "
        f"phase1_boss_certain={summary['phase1_boss_certain']} "
        f"({summary['phase1_elapsed']}s)",
        flush=True,
    )
    for mode, m in summary["modes"].items():
        best = m.get("best_all_certain") or m.get("best")
        print(f"{mode}: hits={m['hits']} four={m['four']} | {one_line_board(best)}", flush=True)
    if compare_path:
        print(f"wrote {compare_path}", flush=True)
    if args.json_out:
        print(f"wrote {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
