#!/usr/bin/env python3
"""Convert Pacific Front page/API dumps into solver scenario JSON.

Accepts scenario JSON, keypoint/operation/inventory/settings dumps, HTML with
embedded JSON, or board-text notes. No browser automation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

VULN_ID = {
    100: "antiAircraftFire",
    101: "detection",
    102: "smokes",
    103: "largeCaliber",
    104: "quickFire",
    105: "strikeAircraft",
    "100": "antiAircraftFire",
    "101": "detection",
    "102": "smokes",
    "103": "largeCaliber",
    "104": "quickFire",
    "105": "strikeAircraft",
}
SPEC_ID = {0: "firepower", 1: "survivability", 2: "mobility",
           "0": "firepower", "1": "survivability", "2": "mobility"}
WEAK_ALIAS = {
    "侦察": "detection", "被侦察性": "detection", "detection": "detection",
    "大口径": "largeCaliber", "largeCaliber": "largeCaliber",
    "速射": "quickFire", "quickFire": "quickFire",
    "舰载机突击": "strikeAircraft", "攻击机": "strikeAircraft",
    "strikeAircraft": "strikeAircraft",
    "烟幕": "smokes", "smokes": "smokes", "SmokeScreen": "smokes",
    "防空火力": "antiAircraftFire", "antiAircraftFire": "antiAircraftFire",
    "AntiAircraftFire": "antiAircraftFire",
}
DIR_ALIAS = {
    "forward": "forward", "forward-left": "forwardLeft", "forward-right": "forwardRight",
    "forwardLeft": "forwardLeft", "forwardRight": "forwardRight",
    "left": "left", "right": "right",
    "上": "forward", "正前": "forward", "左": "forwardLeft", "右": "forwardRight",
    "左前": "forwardLeft", "右前": "forwardRight",
}


def norm_dir(d: str) -> str:
    d = str(d).strip()
    if d in DIR_ALIAS:
        return DIR_ALIAS[d]
    key = d.replace("_", "-").lower()
    if key in DIR_ALIAS:
        return DIR_ALIAS[key]
    # forwardleft -> forwardLeft
    compact = key.replace("-", "")
    mapping = {
        "forward": "forward", "forwardleft": "forwardLeft", "forwardright": "forwardRight",
        "left": "left", "right": "right",
    }
    return mapping.get(compact, d)


def _load_any(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in {".html", ".htm"} or "<html" in raw[:500].lower():
        candidates = []
        for m in re.finditer(r"\{[\s\S]*\}", raw):
            chunk = m.group(0)
            if len(chunk) > 200 and any(k in chunk for k in ("enemies", "slots", "ships", "result", "collection")):
                try:
                    candidates.append(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
        for c in candidates:
            if isinstance(c, dict) and "enemies" in c and "ships" in c:
                return c
        if candidates:
            return candidates[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_text": raw, "_path": str(path)}


def _unwrap(data: Any) -> dict:
    if not isinstance(data, dict):
        return {"_raw": data}
    if "result" in data and isinstance(data["result"], dict):
        return data["result"]
    return data


def _canon_weak(w: Any) -> str:
    if w in VULN_ID:
        return VULN_ID[w]
    s = str(w)
    return WEAK_ALIAS.get(s, VULN_ID.get(s, s))


def settings_card_to_ship(card: dict, *, name: str | None = None, hp: int = 3,
                          banned: bool = False, is_played: bool = False) -> dict:
    specs = card.get("specs") or {}
    fp = float(specs.get("0", specs.get(0, card.get("firepower", 0))))
    surv = float(specs.get("1", specs.get(1, card.get("survivability", 0))))
    mob = float(specs.get("2", specs.get(2, card.get("mobility", 0))))
    effects = []
    for b in card.get("buffs") or []:
        effects.append({
            "kind": "buff",
            "direction": norm_dir(b["direction"]),
            "type": SPEC_ID.get(b.get("spec"), b.get("type", "firepower")),
            "value": float(b.get("value", 0.3)),
        })
    for d in card.get("debuffs") or []:
        et = d.get("type")
        if et is None and "vuln" in d:
            et = _canon_weak(d["vuln"])
        effects.append({
            "kind": "debuff",
            "direction": norm_dir(d["direction"]),
            "type": _canon_weak(et) if et is not None else "detection",
            "value": float(d.get("value", 0.2)),
        })
    # Already-normalized effects list
    for e in card.get("effects") or []:
        if e.get("kind") and e.get("direction"):
            effects.append({
                "kind": e["kind"],
                "direction": norm_dir(e["direction"]),
                "type": _canon_weak(e.get("type") or e.get("effect_type")),
                "value": float(e.get("value", 0.3 if e["kind"] == "buff" else 0.2)),
            })
    code = card.get("code")
    stype = card.get("type") or card.get("ship_type")
    out = {
        "name": name or card.get("name") or code or "ship",
        "firepower": fp,
        "survivability": surv,
        "mobility": mob,
        "hp": int(hp),
        "effects": effects,
        "preserve_priority": card.get("preserve_priority", fp + surv + mob),
        "type": stype,
        "banned": bool(banned),
    }
    if code:
        out["code"] = code
    if is_played:
        out["isPlayed"] = True
    return out


def enemies_from_keypoint(kp: dict, names: dict[str, str] | None = None) -> list[dict]:
    names = names or {}
    slots = kp.get("slots") or {}
    if isinstance(slots, dict):
        items = sorted(slots.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else str(kv[0]))
        slot_vals = [v for _, v in items]
    else:
        slot_vals = list(slots)
    enemies = []
    for slot in slot_vals:
        if not slot or not slot.get("enemy"):
            continue
        e = slot["enemy"]
        code = e.get("code") or e.get("name") or "enemy"
        specs = e.get("specs") or {}
        power = e.get("power")
        if power is None:
            power = float(specs.get("0", specs.get(0, sum(float(v) for v in specs.values()) if specs else 0)))
        vulns = e.get("vulns") or e.get("weaknesses") or {}
        if isinstance(vulns, dict):
            weaknesses = [_canon_weak(k) for k in vulns.keys()]
        else:
            weaknesses = [_canon_weak(x) for x in vulns]
        reward = 0.0
        for r in slot.get("rewards") or e.get("rewards") or []:
            if isinstance(r, dict):
                reward += float(r.get("amount") or 0)
        enemies.append({
            "name": e.get("name") or names.get(code, code),
            "power": float(power),
            "weaknesses": weaknesses,
            "reward": reward,
            "code": code,
            "type": e.get("type"),
        })
    return enemies


def banned_types_from_operation(op: dict, keypoint_id: str | None = None) -> list[str]:
    banned: list[str] = []
    for ef in op.get("effects") or []:
        payload = ef.get("payload") or {}
        if payload.get("type") not in (6, "6", "EXTRA_RESTRICTIONS"):
            continue
        src = str(ef.get("source") or "")
        apply = json.dumps(ef.get("applyTo") or [])
        if keypoint_id and keypoint_id not in src and keypoint_id not in apply:
            # still collect if source matches later; skip unrelated KP effects
            if keypoint_id and src and keypoint_id not in src:
                continue
        for t in (payload.get("restrictions") or {}).get("types") or []:
            if t not in banned:
                banned.append(t)
    return banned


def parse_board_text(text: str) -> dict:
    enemies = []
    for m in re.finditer(
        r"敌\s*(\S+)\s*(?:裸)?战力\s*(\d+)(?:[^（(]*[（(]弱点[：:]\s*([^）)]+)[）)])?.*?奖励[^\d]*(\d+)?",
        text,
    ):
        name, power, weaks, reward = m.group(1), float(m.group(2)), m.group(3) or "", m.group(4)
        weaknesses = [_canon_weak(p) for p in re.split(r"[、,/，\s]+", weaks) if p.strip()]
        enemies.append({"name": name, "power": power, "weaknesses": weaknesses,
                        "reward": float(reward) if reward else 0})
    ships = []
    for m in re.finditer(
        r"([A-Za-z0-9USS HMS\u4e00-\u9fff·\-]+?)\s+(\d+)[–\-−](\d+).*?火力\s*(\d+)\s*/\s*存活性\s*(\d+)\s*/\s*机动\s*(\d+)",
        text,
    ):
        fp, surv, mob = float(m.group(4)), float(m.group(5)), float(m.group(6))
        ships.append({
            "name": m.group(1).strip(), "firepower": fp, "survivability": surv, "mobility": mob,
            "effects": [], "preserve_priority": fp + surv + mob,
            "_note": "stats_as_shown_may_include_neighbor_buffs",
        })
    gaps = []
    if not enemies:
        gaps.append("no enemies parsed from board text")
    if len(ships) < 4:
        gaps.append(f"only {len(ships)} ships with full stats in board text; fleet incomplete")
    return {"enemies": enemies, "ships": ships,
            "meta": {"source": "board_text", "gaps": gaps, "incomplete": True}}



def region_specs_from_operation(op: dict, keypoint_id: str | None = None) -> dict[str, float]:
    """Sum type-1 region stat deltas for this keypoint (player ships).

    Net each effect as specs[k] + delta_specs[k] so subsequent mitigable effects
    that have been partially/fully cancelled still resolve correctly.
    """
    out: dict[str, float] = {}
    for ef in op.get("effects") or []:
        apply = ef.get("applyTo") or []
        src = str(ef.get("source") or "")
        # Prefer applyTo; only fall back to source when applyTo is empty.
        # (Using source alone would wrongly pull effects this KP emits onto others.)
        if keypoint_id:
            if apply:
                if keypoint_id not in apply:
                    continue
            elif keypoint_id not in src:
                continue
        payload = ef.get("payload") or {}
        if payload.get("type") not in (1, "1"):
            continue
        specs = payload.get("specs") or {}
        delta = payload.get("delta_specs") or {}
        keys = set(str(k) for k in specs) | set(str(k) for k in delta)
        for k in keys:
            raw = float(specs.get(k, specs.get(int(k) if str(k).isdigit() else k, 0)) or 0)
            # delta may use same key types
            dlt = float(delta.get(k, delta.get(int(k) if str(k).isdigit() else k, 0)) or 0)
            net = raw + dlt
            if abs(net) < 1e-12:
                continue
            sk = SPEC_ID.get(k, SPEC_ID.get(str(k), str(k)))
            out[sk] = out.get(sk, 0.0) + net
    return out


def enemy_region_power_from_operation(
    op: dict, keypoint_id: str | None = None
) -> list[dict]:
    """Type-4 region effects: enemy combat-power % buffs (e.g. 岸防炮 +16%).

    Live payload shape (KP with 防空炮 169→196):
      type=4, specs={0:0.16,1:0.16,2:0.16},
      apply_restrictions.types = ship types that do NOT receive the buff
        (coastal_artillery omitted → only coastal gets +16%).
    If restrictions.types is set, treat it as an include whitelist instead.
    Multiple type-4 effects stack by summing power deltas.
    """
    out: list[dict] = []
    for ef in op.get("effects") or []:
        apply = ef.get("applyTo") or []
        src = str(ef.get("source") or "")
        if keypoint_id:
            if apply:
                if keypoint_id not in apply:
                    continue
            elif keypoint_id not in src:
                continue
        payload = ef.get("payload") or {}
        if payload.get("type") not in (4, "4"):
            continue
        specs = payload.get("specs") or {}
        delta = payload.get("delta_specs") or {}
        # Power% from spec 0 (enemy power is specs["0"]); fall back to mean.
        vals = []
        for k in sorted(set(list(specs.keys()) + list(delta.keys())), key=lambda x: str(x)):
            raw = float(specs.get(k, 0) or 0) + float(delta.get(k, 0) or 0)
            vals.append(raw)
        if not vals:
            continue
        # Prefer firepower / "0"
        if 0 in specs or "0" in specs or 0 in delta or "0" in delta:
            power_delta = float(specs.get(0, specs.get("0", 0)) or 0) + float(
                delta.get(0, delta.get("0", 0)) or 0
            )
        else:
            power_delta = sum(vals) / len(vals)
        if abs(power_delta) < 1e-12:
            continue
        include = None
        exclude = None
        restr = payload.get("restrictions")
        if isinstance(restr, dict) and restr.get("types"):
            include = [str(t) for t in restr["types"]]
        apply_r = payload.get("apply_restrictions")
        if include is None and isinstance(apply_r, dict) and apply_r.get("types"):
            # Observed: listed types are exempt → buff the complement (岸防炮/防空炮)
            exclude = [str(t) for t in apply_r["types"]]
        out.append({
            "power_delta": power_delta,
            "include_types": include,
            "exclude_types": exclude,
        })
    return out


def apply_enemy_region_power(enemies: list[dict], buffs: list[dict]) -> None:
    """Multiply enemy power by stacked (1+delta) for matching type filters."""
    if not enemies or not buffs:
        return
    for e in enemies:
        if "_bare_power" not in e:
            e["_bare_power"] = float(e.get("power") or 0)
        et = str(e.get("type") or "")
        code = str(e.get("code") or "")
        name = str(e.get("name") or "")
        mult = 1.0
        for b in buffs:
            delta = float(b.get("power_delta") or 0)
            if abs(delta) < 1e-12:
                continue
            include = b.get("include_types")
            exclude = b.get("exclude_types")
            ok = True
            if include is not None:
                ok = et in include or code in include or name in include
            elif exclude is not None:
                ok = et not in exclude and code not in exclude
            if ok:
                mult *= 1.0 + delta
        e["power"] = float(e["_bare_power"]) * mult


def buff_multiplier_from_operation(op: dict, keypoint_id: str | None = None) -> float | None:
    """Type-2 payload.multiplier on this keypoint (0.0 => 增益效果 disabled).

    Prefer applyTo match. Net multiplier + delta_multiplier for subsequent cancels.
    """
    found = None
    for ef in op.get("effects") or []:
        apply = ef.get("applyTo") or []
        src = str(ef.get("source") or "")
        if keypoint_id:
            if apply:
                if keypoint_id not in apply:
                    continue
            elif keypoint_id not in src:
                continue
        payload = ef.get("payload") or {}
        if payload.get("type") not in (2, "2"):
            continue
        if "multiplier" in payload:
            found = float(payload["multiplier"])
    return found


def battle_conditions_from_operation(
    op: dict | None,
    keypoint: dict | None = None,
    keypoint_id: str | None = None,
) -> dict:
    """Collect start-gate style battle conditions; flag needs_unstoppable when present.

    Stat region (type 1) and buff multiplier (type 2) are already applied numerically
    and do not block starting. EXTRA_RESTRICTIONS (type 6) bans ship types (handled
    separately). Other effect payloads / keypoint condition fields are treated as
    start conditions that 势不可挡 can bypass.
    """
    conditions: list[dict] = []
    needs = False
    kid = keypoint_id or ((keypoint or {}).get("id") if keypoint else None)

    if isinstance(keypoint, dict):
        for key in ("battleConditions", "conditions", "startConditions", "battle_conditions"):
            raw = keypoint.get(key)
            if raw:
                conditions.append({"source": "keypoint", "key": key, "raw": raw})
                needs = True
        # Some builds expose a boolean / list under requirements
        req = keypoint.get("requirements") or keypoint.get("startRequirements")
        if req:
            conditions.append({"source": "keypoint", "key": "requirements", "raw": req})
            needs = True

    for ef in (op or {}).get("effects") or []:
        if ef.get("isSubsequent"):
            continue
        apply = ef.get("applyTo") or []
        src = str(ef.get("source") or "")
        if kid and kid not in apply and kid not in src:
            continue
        payload = ef.get("payload") or {}
        ptype = payload.get("type")
        # 1 = player region specs, 2 = buff multiplier, 4 = enemy power% — not start gates
        if ptype in (1, "1", 2, "2", 4, "4"):
            continue
        # 6 = EXTRA_RESTRICTIONS (banned types) — does not itself grey out Start;
        # still record for UI, but do not force needs_unstoppable.
        entry = {
            "source": "operation",
            "effect_type": ptype,
            "payload": {k: payload.get(k) for k in list(payload)[:8]},
        }
        conditions.append(entry)
        if ptype not in (6, "6", "EXTRA_RESTRICTIONS", None):
            needs = True
        # Explicit gate flags some builds use
        if payload.get("blockStart") or payload.get("requiresUnstoppable"):
            needs = True

    return {
        "battle_conditions": conditions,
        "needs_unstoppable": bool(needs),
    }


def apply_region_to_ships(ships: list[dict], region: dict[str, float]) -> None:
    """Multiply bare combat stats by (1+delta) once; keep _bare_* copies."""
    if not region:
        return
    for s in ships:
        for key, bare_key in (
            ("firepower", "_bare_firepower"),
            ("survivability", "_bare_survivability"),
            ("mobility", "_bare_mobility"),
        ):
            if bare_key not in s:
                s[bare_key] = float(s.get(key, 0))
            delta = float(region.get(key, 0.0))
            s[key] = float(s[bare_key]) * (1.0 + delta)




def health_factor_from_settings(settings: dict, default: list[float] | None = None) -> list[float]:
    """Return [HP1, HP2, HP3] multipliers from settings.defaultCoreSettings.HealthFactor."""
    default = default or [0.8, 0.9, 1.0]
    opts = (settings or {}).get("options") or {}
    core = opts.get("defaultCoreSettings") or opts.get("coreSettings") or {}
    hf = core.get("HealthFactor")
    if isinstance(hf, list) and len(hf) >= 3:
        return [float(hf[0]), float(hf[1]), float(hf[2])]
    return list(default)


def apply_health_factor_to_ships(ships: list[dict], health_factor: list[float]) -> None:
    """Scale combat stats by inventory HP via HealthFactor[hp-1]. Keep bare_* and preserve_priority on full catalog."""
    if not health_factor or not ships:
        return
    for s in ships:
        hp = int(s.get("hp", 3))
        idx = max(1, min(3, hp)) - 1
        factor = float(health_factor[idx])
        s["_health_factor"] = factor
        for key, bare_key in (
            ("firepower", "_bare_firepower"),
            ("survivability", "_bare_survivability"),
            ("mobility", "_bare_mobility"),
        ):
            if bare_key not in s:
                s[bare_key] = float(s.get(key, 0))
            # preserve_priority stays on full (pre-HP) sum if not set specially
            s[key] = float(s[bare_key]) * factor
        if s.get("preserve_priority") is None:
            s["preserve_priority"] = (
                float(s.get("_bare_firepower", 0))
                + float(s.get("_bare_survivability", 0))
                + float(s.get("_bare_mobility", 0))
            )

def merge_dumps(parts: list[dict]) -> dict:
    scenario: dict[str, Any] = {"enemies": [], "ships": [], "meta": {"sources": [], "gaps": []}}
    catalog: dict[str, dict] = {}
    locale_cards: dict[str, str] = {}
    locale_enemies: dict[str, str] = {}
    inventory_cards: list[dict] = []
    keypoint = None
    operation = None
    banned_types: list[str] = []
    health_factor: list[float] = [0.8, 0.9, 1.0]
    settings_blob: dict | None = None

    for part in parts:
        data = _unwrap(part) if isinstance(part, dict) else part
        if not isinstance(data, dict):
            continue

        # Locale name map dump
        if "cards" in data and "enemies" in data and isinstance(data["cards"], dict) and not data.get("specs"):
            if all(isinstance(v, str) for v in list(data["cards"].values())[:3] or [""]):
                locale_cards.update(data["cards"])
                locale_enemies.update(data.get("enemies") or {})
                scenario["meta"]["sources"].append("locale")
                continue

        if "enemies" in data and "ships" in data and isinstance(data["ships"], list):
            scenario["enemies"] = data["enemies"] or scenario["enemies"]
            scenario["ships"] = [settings_card_to_ship(s, name=s.get("name"), hp=s.get("hp", 3),
                                                       banned=bool(s.get("banned")),
                                                       is_played=bool(s.get("isPlayed")))
                                 for s in data["ships"]]
            scenario["meta"]["sources"].append("scenario")
            if data.get("banned_types"):
                banned_types = list(data["banned_types"])
            if data.get("meta"):
                scenario["meta"].update({k: v for k, v in data["meta"].items() if k != "sources"})
            continue

        # Settings with collection.cards
        if "collection" in data and isinstance(data["collection"], dict):
            settings_blob = data
            for c in data["collection"].get("cards") or []:
                if isinstance(c, dict) and c.get("code"):
                    catalog[c["code"]] = c
            health_factor = health_factor_from_settings(data, health_factor)
            scenario["meta"]["sources"].append("settings")
            continue

        if "cards" in data and isinstance(data["cards"], list) and data["cards"] and "specs" in (data["cards"][0] or {}):
            for c in data["cards"]:
                catalog[c["code"]] = c
            scenario["meta"]["sources"].append("catalog")
            continue

        if "cards" in data and isinstance(data["cards"], list) and data["cards"] and "health" in (data["cards"][0] or {}):
            inventory_cards = data["cards"]
            scenario["meta"]["sources"].append("inventory")
            continue

        if "slots" in data and isinstance(data.get("slots"), (dict, list)):
            keypoint = data
            scenario["meta"]["sources"].append(f"keypoint:{data.get('id', '?')}")
            continue

        if "stages" in data and isinstance(data.get("stages"), list):
            operation = data
            scenario["meta"]["sources"].append(f"operation:{data.get('id', '?')}")
            continue

        if "_text" in data:
            parsed = parse_board_text(data["_text"])
            if parsed["enemies"] and not scenario["enemies"]:
                scenario["enemies"] = parsed["enemies"]
            if parsed["ships"] and not scenario["ships"]:
                scenario["ships"] = parsed["ships"]
            scenario["meta"]["gaps"].extend(parsed["meta"].get("gaps") or [])
            scenario["meta"]["sources"].append("board_text")

    if keypoint and not scenario["enemies"]:
        scenario["enemies"] = enemies_from_keypoint(keypoint, locale_enemies)
        scenario["meta"]["keypoint_id"] = keypoint.get("id")
        scenario["meta"]["keypoint_name"] = keypoint.get("name")

    if operation:
        kid = (keypoint or {}).get("id") or scenario["meta"].get("keypoint_id")
        banned_types = banned_types or banned_types_from_operation(operation, kid)
        if not scenario["enemies"] and kid:
            for st in operation.get("stages") or []:
                for kp in st.get("keypoints") or []:
                    if kp.get("id") == kid:
                        scenario["enemies"] = enemies_from_keypoint(kp, locale_enemies)

    if catalog and inventory_cards and not scenario["ships"]:
        for ic in inventory_cards:
            code = ic.get("code")
            if not code or code not in catalog:
                continue
            stype = catalog[code].get("type")
            banned = (stype in banned_types) or bool(ic.get("isPlayed"))
            scenario["ships"].append(settings_card_to_ship(
                catalog[code],
                name=locale_cards.get(code, code),
                hp=int(ic.get("health", 3)),
                banned=banned,
                is_played=bool(ic.get("isPlayed")),
            ))
    elif catalog and not scenario["ships"]:
        for code, card in catalog.items():
            scenario["ships"].append(settings_card_to_ship(
                card, name=locale_cards.get(code, code), banned=card.get("type") in banned_types
            ))
        scenario["meta"]["gaps"].append("ships from catalog only (no inventory HP/isPlayed)")

    if banned_types:
        scenario["banned_types"] = banned_types
        for s in scenario["ships"]:
            if s.get("type") in banned_types:
                s["banned"] = True

    # Apply locale names onto enemies if still codes
    if locale_enemies:
        for e in scenario["enemies"]:
            code = e.get("code")
            if code and code in locale_enemies:
                e["name"] = locale_enemies[code]

    # Inventory HP scaling (HealthFactor) before region modifiers.
    # After scale, refresh _bare_* to HP-scaled values so region stacks on current stats.
    if scenario["ships"]:
        apply_health_factor_to_ships(scenario["ships"], health_factor)
        for s in scenario["ships"]:
            for key in ("firepower", "survivability", "mobility"):
                s[f"_bare_{key}"] = float(s.get(key, 0))
        scenario["meta"]["health_factor"] = list(health_factor)

    # Region / global battle conditions from operation effects.
    # Type-1 player specs and type-4 enemy power% use specs+delta_specs netting so
    # subsequent mitigable effects cancel correctly when cleared.
    if operation:
        kid = (keypoint or {}).get("id") or scenario["meta"].get("keypoint_id")
        region = region_specs_from_operation(operation, kid)
        if region and scenario["ships"]:
            apply_region_to_ships(scenario["ships"], region)
            scenario["meta"]["region_modifiers"] = region
        enemy_buffs = enemy_region_power_from_operation(operation, kid)
        if enemy_buffs and scenario["enemies"]:
            apply_enemy_region_power(scenario["enemies"], enemy_buffs)
            scenario["meta"]["enemy_region_power"] = enemy_buffs
        buff_m = buff_multiplier_from_operation(operation, kid)
        if buff_m is not None:
            scenario["buff_multiplier"] = buff_m
            if buff_m == 0:
                scenario["disable_buffs"] = True
            scenario["meta"]["buff_multiplier"] = buff_m
        bc = battle_conditions_from_operation(operation, keypoint, kid)
        if bc.get("battle_conditions"):
            scenario["meta"]["battle_conditions"] = bc["battle_conditions"]
        if bc.get("needs_unstoppable"):
            scenario["needs_unstoppable"] = True
            scenario["meta"]["needs_unstoppable"] = True

    if not scenario["enemies"]:
        scenario["meta"]["gaps"].append("missing enemies — provide keypoint dump or scenario")
    if not scenario["ships"]:
        scenario["meta"]["gaps"].append(
            "missing ships — provide settings+inventory (+locale) or a scenario with ships"
        )

    scenario["meta"]["incomplete"] = bool(scenario["meta"].get("gaps"))
    return scenario


def build_scenario_from_paths(paths: list[Path]) -> dict:
    return merge_dumps([_load_any(p) for p in paths])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape Pacific Front dumps into solver scenario JSON")
    ap.add_argument("--from-dump", action="append", default=[], dest="dumps",
                    help="dump path (repeatable)")
    ap.add_argument("--from-json", dest="from_json", help="alias for a single dump path")
    ap.add_argument("-o", "--output", help="write scenario JSON here")
    ap.add_argument("--solve", action="store_true", help="run exhaustive solver after scrape")
    ap.add_argument("-n", "--top", type=int, default=20)
    ap.add_argument("--json-out")
    ap.add_argument("--exclude-type", action="append", default=[])
    ap.add_argument("--include-banned", action="store_true")
    ap.add_argument("--disable-buffs", action="store_true",
                    help="force 增益效果 disabled (neighbor buffs off)")
    ap.add_argument("--buff-multiplier", type=float, default=None,
                    help="override neighbor buff multiplier (0 = disabled)")
    ap.add_argument("--region", action="append", default=[],
                    help="force region delta STAT=DELTA, e.g. firepower=-0.08 (repeatable)")
    ap.add_argument("--allow-partial", action="store_true")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in args.dumps]
    if args.from_json:
        paths.append(Path(args.from_json))
    if not paths:
        ap.error("provide --from-dump PATH")

    for p in paths:
        if not p.exists():
            print(f"missing dump: {p}", file=sys.stderr)
            return 2

    scenario = build_scenario_from_paths(paths)
    # Optional CLI overrides for battle conditions not present in API dumps
    region_override: dict = {}
    for item in args.region:
        k, _, v = item.partition("=")
        region_override[k.strip()] = float(v)
    if region_override and scenario.get("ships"):
        # re-apply from bare if already adjusted, else from current
        for s in scenario["ships"]:
            for key in ("firepower", "survivability", "mobility"):
                bare = s.get(f"_bare_{key}", s.get(key))
                s[f"_bare_{key}"] = float(bare)
                s[key] = float(bare)  # reset before apply
        apply_region_to_ships(scenario["ships"], region_override)
        scenario["meta"]["region_modifiers"] = {
            **(scenario["meta"].get("region_modifiers") or {}),
            **region_override,
            "source": "cli",
        }
    if args.disable_buffs:
        scenario["disable_buffs"] = True
        scenario["buff_multiplier"] = 0.0
        scenario["meta"]["disable_buffs"] = True
    if args.buff_multiplier is not None:
        scenario["buff_multiplier"] = float(args.buff_multiplier)
        scenario["disable_buffs"] = float(args.buff_multiplier) == 0.0
        scenario["meta"]["buff_multiplier"] = float(args.buff_multiplier)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        json.dump(scenario, open(args.output, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"wrote scenario {args.output} enemies={len(scenario['enemies'])} ships={len(scenario['ships'])}")
    else:
        print(json.dumps({
            "enemies": len(scenario["enemies"]),
            "ships": len(scenario["ships"]),
            "banned_types": scenario.get("banned_types"),
            "meta": scenario.get("meta"),
        }, ensure_ascii=False, indent=2))

    if scenario["meta"].get("gaps"):
        print("GAPS:", "; ".join(scenario["meta"]["gaps"]), file=sys.stderr)

    if args.solve:
        if not scenario["enemies"] or not scenario["ships"]:
            print("cannot solve: incomplete scenario", file=sys.stderr)
            return 1
        from solver import filter_pool, format_solution, parse_scenario, solve
        ships, enemies, meta = parse_scenario(scenario)
        exclude = list(args.exclude_type) or list(scenario.get("banned_types") or meta.get("banned_types") or [])
        if not exclude and any(s.banned and (s.ship_type or "").lower() == "cruiser" for s in ships):
            exclude = ["cruiser"]
        disable_buffs = bool(scenario.get("disable_buffs") or meta.get("disable_buffs"))
        buff_multiplier = float(
            scenario.get("buff_multiplier", meta.get("buff_multiplier", 0.0 if disable_buffs else 1.0))
        )
        sols = solve(
            ships,
            enemies,
            fill_all=not args.allow_partial,
            exclude_banned=not args.include_banned,
            exclude_types=exclude,
            disable_buffs=disable_buffs,
            buff_multiplier=buff_multiplier,
        )
        pool_n = len(filter_pool(ships, exclude_banned=not args.include_banned, exclude_types=exclude))
        print(f"pool={pool_n} certain solutions: {len(sols)} disable_buffs={disable_buffs} buff_mult={buff_multiplier}")
        for i, s in enumerate(sols[: args.top], 1):
            print()
            print(format_solution(s, i))
        if args.json_out:
            payload = {"pool": pool_n, "certain_count": len(sols),
                       "exclude_types": exclude, "solutions": sols[: args.top]}
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            json.dump(payload, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
