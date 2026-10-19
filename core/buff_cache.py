"""In-memory operation buff cache.

Design: a logical table kept in memory (no spreadsheet / visible grid). Built from
``GET /api/v2/operation/{id}/`` and refreshed incrementally — enemy board power is
recomputed only for keypoints whose bare board or applied region effects changed.

``enemy_power`` is the board total after stacking type-4 region combat-power buffs
on bare slot power, using the same rules as ``core.scrape.apply_enemy_region_power``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .resource_summary import progress_rewards_on_keypoint
from .scrape import (
    apply_enemy_region_power,
    enemies_from_keypoint,
    enemy_region_power_from_operation,
)

CLEARED_STATES = {
    "cleared",
    "completed",
    "complete",
    "done",
    "destroyed",
    "captured",
    "finished",
    "success",
}
LOCKED_STATES = {
    "locked",
    "closed",
    "unavailable",
    "hidden",
    "blocked",
    "sealed",
}
AVAILABLE_STATES = {
    "available",
    "opened",
    "open",
    "active",
    "current",
    "ready",
    "selectable",
    "unlocked",
    "in_progress",
    "attackable",
}


def unwrap_operation(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    inner = data.get("result")
    if isinstance(inner, dict) and (
        "stages" in inner or "effects" in inner or "id" in inner
    ):
        return inner
    return data


def keypoints_of(stage: dict) -> list[dict]:
    raw = stage.get("keypoints")
    if not isinstance(raw, list):
        raw = stage.get("keyPoints")
    if not isinstance(raw, list):
        return []
    return [kp for kp in raw if isinstance(kp, dict)]


def stages_of(op: dict) -> list[dict]:
    raw = op.get("stages") or []
    return [st for st in raw if isinstance(st, dict)]


def _truthy_flag(kp: dict, *keys: str) -> bool:
    for k in keys:
        if kp.get(k) in (True, 1, "1", "true", "True"):
            return True
    return False


def keypoint_status(kp: dict) -> str:
    """Normalize live keypoint state to cleared / locked / available."""
    if _truthy_flag(kp, "isCleared", "cleared", "completed", "isCompleted", "isDestroyed"):
        return "cleared"
    raw = str(kp.get("status") or kp.get("state") or kp.get("availability") or "").strip().lower()
    if raw in CLEARED_STATES:
        return "cleared"
    if raw in LOCKED_STATES or _truthy_flag(kp, "isLocked", "locked"):
        return "locked"
    if raw in AVAILABLE_STATES or _truthy_flag(kp, "isCurrent", "canAttack", "attackable"):
        return "available"
    if "lock" in raw or raw in {"closed"}:
        return "locked"
    return "available"


def is_optional_keypoint(kp: dict) -> bool:
    if _truthy_flag(kp, "optional", "isOptional", "side", "isSide"):
        return True
    role = str(kp.get("role") or kp.get("kind") or kp.get("nodeType") or kp.get("type") or "").lower()
    return role in {"optional", "side", "bonus", "extra"}


def _num(obj: dict, *keys: str) -> float | None:
    for k in keys:
        if k in obj and obj[k] is not None:
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                continue
    return None


def stage_destroy_goal(stage: dict) -> dict[str, Any]:
    """How many destroys this stage still needs.

    Reads ``stage.condition`` (type ``cleared_stage`` and similar) ``target`` /
    ``total``. ``current`` prefers an explicit counter, otherwise the number of
    keypoints already cleared. ``remaining`` is what the automation prints after
    every battle.
    """
    cond = stage.get("condition") or stage.get("goal") or stage.get("objective") or {}
    if isinstance(cond, list):
        cond = next((c for c in cond if isinstance(c, dict)), {})
    if not isinstance(cond, dict):
        cond = {}
    prog = stage.get("progress") if isinstance(stage.get("progress"), dict) else {}
    ctype = str(cond.get("type") or cond.get("kind") or prog.get("type") or "cleared_stage")
    target = _num(cond, "target", "amount", "need", "required", "count")
    if target is None:
        target = _num(prog, "target", "amount", "required", "need")
    total = _num(cond, "total", "max", "of")
    if total is None:
        total = _num(prog, "total", "max")
    current = _num(cond, "current", "progress", "value", "cleared")
    if current is None:
        current = _num(prog, "current", "value", "cleared")

    kps = keypoints_of(stage)
    cleared = sum(1 for kp in kps if keypoint_status(kp) == "cleared")
    n = len(kps)
    if current is None:
        current = float(cleared)
    if total is None:
        total = float(n)
    if target is None:
        target = float(n)
    remaining = max(0.0, float(target) - float(current))
    return {
        "type": ctype,
        "target": float(target),
        "total": float(total),
        "current": float(current),
        "remaining": remaining,
        "met": float(current) + 1e-9 >= float(target),
        "cleared_keypoints": cleared,
        "keypoint_count": n,
    }


def _effect_source_ids(ef: dict) -> set[str]:
    src = ef.get("source")
    out: set[str] = set()
    if isinstance(src, str) and src:
        out.add(src)
    elif isinstance(src, dict):
        for k in ("id", "keypointId", "keypoint_id"):
            if src.get(k):
                out.add(str(src[k]))
    elif isinstance(src, list):
        for item in src:
            if isinstance(item, str):
                out.add(item)
            elif isinstance(item, dict) and item.get("id"):
                out.add(str(item["id"]))
    return out


def _applies_to(ef: dict, kp_id: str) -> bool:
    apply = ef.get("applyTo") or ef.get("apply_to") or []
    if isinstance(apply, str):
        apply = [apply]
    if apply:
        for item in apply:
            if item == kp_id or (isinstance(item, str) and kp_id in item):
                return True
            if isinstance(item, dict) and str(item.get("id") or "") == kp_id:
                return True
        return False
    return kp_id in _effect_source_ids(ef) or kp_id in str(ef.get("source") or "")


def _spec_nets(payload: dict) -> list[float]:
    specs = payload.get("specs") or {}
    delta = payload.get("delta_specs") or {}
    keys = set(specs) | set(delta)
    nets = []
    for k in keys:
        try:
            nets.append(float(specs.get(k, 0) or 0) + float(delta.get(k, 0) or 0))
        except (TypeError, ValueError):
            continue
    return nets


def _type4_power_delta(payload: dict) -> float:
    specs = payload.get("specs") or {}
    delta = payload.get("delta_specs") or {}
    if any(k in specs or k in delta for k in (0, "0")):
        return float(specs.get(0, specs.get("0", 0)) or 0) + float(
            delta.get(0, delta.get("0", 0)) or 0
        )
    nets = _spec_nets(payload)
    if not nets:
        return 0.0
    return sum(nets) / len(nets)


def is_harmful_effect(ef: dict) -> bool:
    """Enemy buff or player debuff that clearing the source keypoint removes.

    Subsequent effects are rewards applied after a clear, not live penalties.
    """
    if ef.get("isSubsequent") or ef.get("is_subsequent"):
        return False
    payload = ef.get("payload") or {}
    ptype = payload.get("type")
    if ptype in (4, "4"):
        # Positive combat-power % on enemies (岸防炮 / 防空炮一类).
        return _type4_power_delta(payload) > 1e-9
    if ptype in (1, "1"):
        # Negative region specs reduce our firepower / survivability / mobility.
        return any(n < -1e-9 for n in _spec_nets(payload))
    if ptype in (2, "2"):
        try:
            mult = float(payload.get("multiplier", 1) or 0)
        except (TypeError, ValueError):
            mult = 1.0
        try:
            dmult = float(payload.get("delta_multiplier") or 0)
        except (TypeError, ValueError):
            dmult = 0.0
        # multiplier < 1 weakens our neighbor buffs (增益效果被削减).
        return (mult + dmult) < 1.0 - 1e-9
    return False


def cuts_harmful_effects(kp_id: str, effects: list[dict]) -> bool:
    for ef in effects:
        if not isinstance(ef, dict):
            continue
        if kp_id not in _effect_source_ids(ef) and str(ef.get("source") or "") != kp_id:
            # source string may embed the id ("keypoint:<uuid>")
            if kp_id not in str(ef.get("source") or ""):
                continue
        if is_harmful_effect(ef):
            return True
    return False


def _json_sig(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def bare_board(kp: dict) -> tuple[float, str, list[dict]]:
    enemies = enemies_from_keypoint(kp)
    bare = 0.0
    sig_rows = []
    for e in enemies:
        p = float(e.get("_bare_power", e.get("power") or 0) or 0)
        # enemies_from_keypoint stores raw power; region apply happens later.
        if "_bare_power" in e:
            p = float(e["_bare_power"])
        bare += p
        sig_rows.append({
            "code": e.get("code"),
            "type": e.get("type"),
            "power": p,
        })
    return bare, _json_sig(sig_rows), enemies


def board_enemy_power(kp: dict, op: dict) -> tuple[float, float]:
    """Return (bare_sum, region-buffed board sum) for one keypoint."""
    bare, _sig, enemies = bare_board(kp)
    buffs = enemy_region_power_from_operation(op, kp.get("id"))
    apply_enemy_region_power(enemies, buffs)
    total = sum(float(e.get("power") or 0) for e in enemies)
    return bare, total


def _power_effect_sig(op: dict, kp_id: str) -> str:
    """Signature of type-4 (and any other) effects that apply to this keypoint."""
    rows = []
    for ef in op.get("effects") or []:
        if not isinstance(ef, dict):
            continue
        if not _applies_to(ef, kp_id):
            continue
        payload = ef.get("payload") or {}
        rows.append({
            "source": ef.get("source"),
            "applyTo": ef.get("applyTo"),
            "isSubsequent": bool(ef.get("isSubsequent")),
            "type": payload.get("type"),
            "specs": payload.get("specs"),
            "delta_specs": payload.get("delta_specs"),
            "multiplier": payload.get("multiplier"),
            "delta_multiplier": payload.get("delta_multiplier"),
            "restrictions": payload.get("restrictions"),
            "apply_restrictions": payload.get("apply_restrictions"),
        })
    return _json_sig(rows)


def _last_keypoint_id(op: dict) -> str | None:
    stages = stages_of(op)
    if not stages:
        return None
    last_stage = stages[-1]
    kps = keypoints_of(last_stage)
    if not kps:
        return None
    for kp in kps:
        if _truthy_flag(kp, "final", "isFinal", "isLast", "boss", "isBoss"):
            kid = kp.get("id")
            if kid:
                return str(kid)
    kid = kps[-1].get("id")
    return str(kid) if kid else None


@dataclass
class KpRow:
    id: str
    name: str
    stage_index: int
    stage_id: str
    order: int
    status: str
    optional: bool
    is_last: bool
    bare_power: float
    enemy_power: float
    cuts_harm: bool
    progress_lanes: list[int]
    progress_rewards: list[dict]
    power_sig: str
    bare_sig: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "stage_index": self.stage_index,
            "stage_id": self.stage_id,
            "order": self.order,
            "status": self.status,
            "optional": self.optional,
            "is_last": self.is_last,
            "bare_power": self.bare_power,
            "enemy_power": self.enemy_power,
            "cuts_harm": self.cuts_harm,
            "progress_lanes": list(self.progress_lanes),
            "progress_rewards": list(self.progress_rewards),
        }


@dataclass
class StageView:
    index: int
    id: str
    name: str
    goal: dict
    rows: list[KpRow] = field(default_factory=list)


@dataclass
class BuffCache:
    """Logical buff / keypoint table for one operation."""

    operation_id: str = ""
    operation_name: str = ""
    stages: list[StageView] = field(default_factory=list)
    last_diff: dict = field(default_factory=dict)

    def iter_rows(self):
        for st in self.stages:
            yield from st.rows

    def row(self, kp_id: str) -> KpRow | None:
        for row in self.iter_rows():
            if row.id == kp_id:
                return row
        return None

    @property
    def cleared_count(self) -> int:
        return sum(1 for r in self.iter_rows() if r.status == "cleared")

    @property
    def total_count(self) -> int:
        return sum(1 for _ in self.iter_rows())

    @property
    def is_complete(self) -> bool:
        rows = list(self.iter_rows())
        if not rows:
            return False
        return all(r.status == "cleared" for r in rows)

    def update_from_operation(self, op: dict) -> dict:
        """Rebuild rows. Recompute ``enemy_power`` only when bare board or applied effects change."""
        op = unwrap_operation(op)
        old = {r.id: r for r in self.iter_rows()}
        effects = [ef for ef in (op.get("effects") or []) if isinstance(ef, dict)]
        last_id = _last_keypoint_id(op)
        recomputed: list[str] = []
        unchanged: list[str] = []
        status_changed: list[str] = []

        self.operation_id = str(op.get("id") or self.operation_id or "")
        self.operation_name = str(op.get("name") or op.get("title") or self.operation_name or "")
        new_stages: list[StageView] = []
        order = 0
        for si, st in enumerate(stages_of(op)):
            view = StageView(
                index=si,
                id=str(st.get("id") or f"stage-{si}"),
                name=str(st.get("name") or st.get("title") or f"阶段{si + 1}"),
                goal=stage_destroy_goal(st),
            )
            for kp in keypoints_of(st):
                kid = str(kp.get("id") or "")
                if not kid:
                    continue
                status = keypoint_status(kp)
                bare, bare_sig, _enemies = bare_board(kp)
                power_sig = _power_effect_sig(op, kid)
                prev = old.get(kid)
                if prev is not None and prev.power_sig == power_sig and prev.bare_sig == bare_sig:
                    enemy_power = prev.enemy_power
                    bare_power = prev.bare_power
                    unchanged.append(kid)
                else:
                    bare_power, enemy_power = board_enemy_power(kp, op)
                    recomputed.append(kid)
                if prev is not None and prev.status != status:
                    status_changed.append(kid)
                rewards = progress_rewards_on_keypoint(kp)
                view.rows.append(KpRow(
                    id=kid,
                    name=str(kp.get("name") or kp.get("title") or kid[:8]),
                    stage_index=si,
                    stage_id=view.id,
                    order=order,
                    status=status,
                    optional=is_optional_keypoint(kp),
                    is_last=bool(last_id and kid == last_id),
                    bare_power=round(float(bare_power), 4),
                    enemy_power=round(float(enemy_power), 4),
                    cuts_harm=cuts_harmful_effects(kid, effects),
                    progress_lanes=[int(r["lane"]) for r in rewards],
                    progress_rewards=rewards,
                    power_sig=power_sig,
                    bare_sig=bare_sig,
                ))
                order += 1
            new_stages.append(view)
        self.stages = new_stages
        diff = {
            "recomputed": recomputed,
            "unchanged": unchanged,
            "status_changed": status_changed,
        }
        self.last_diff = diff
        return diff

    @classmethod
    def from_operation(cls, op: dict) -> "BuffCache":
        cache = cls()
        cache.update_from_operation(op)
        # First build computes every row; present that explicitly.
        cache.last_diff = {
            "recomputed": [r.id for r in cache.iter_rows()],
            "unchanged": [],
            "status_changed": [],
        }
        return cache
