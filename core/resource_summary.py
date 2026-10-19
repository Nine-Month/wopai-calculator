"""Reward summaries taken from operation keypoint slot ``rewards``.

Progress-token codes include ``adeck_progress_points`` (进度分) and close aliases.
Used by the automation summary and by the last-keypoint 效率提升 rule.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

# Known progress currencies. Matching is also fuzzy (see is_progress_reward).
PROGRESS_CODES = {
    "adeck_progress_points",
    "adeck_progress_point",
    "adeck_progress_token",
    "adeck_progress_tokens",
    "progress_points",
    "progress_point",
    "progress_token",
    "progress_tokens",
}

CODE_ZH = {
    "adeck_progress_points": "进度分",
    "adeck_progress_token": "进度代币",
    "progress_points": "进度分",
    "steel": "钢材",
    "oil": "石油",
    "gold": "金币",
    "credits": "银币",
    "eventum_1": "活动代币",
    "elite_xp": "精英经验",
}


def reward_code(reward: dict) -> str:
    return str(
        reward.get("code")
        or reward.get("id")
        or reward.get("currency")
        or reward.get("type")
        or reward.get("name")
        or ""
    )


def reward_amount(reward: dict) -> float:
    for k in ("amount", "value", "count", "quantity"):
        if reward.get(k) is not None:
            try:
                return float(reward[k])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def is_progress_reward(reward: dict) -> bool:
    if not isinstance(reward, dict):
        return False
    code = reward_code(reward).strip().lower()
    if code in PROGRESS_CODES:
        return True
    if "progress" in code and any(tok in code for tok in ("point", "token", "adeck", "score")):
        return True
    name = str(reward.get("name") or reward.get("title") or "")
    if "进度" in name and any(tok in name for tok in ("代币", "分", "点")):
        return True
    return False


def iter_slot_pairs(kp: dict) -> Iterable[tuple[int, dict]]:
    slots = kp.get("slots") or {}
    if isinstance(slots, dict):
        items = sorted(
            slots.items(),
            key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else str(kv[0]),
        )
        for i, (_k, slot) in enumerate(items):
            if isinstance(slot, dict):
                yield i, slot
    elif isinstance(slots, list):
        for i, slot in enumerate(slots):
            if isinstance(slot, dict):
                yield i, slot


def progress_rewards_on_keypoint(kp: dict) -> list[dict]:
    """Progress-token rewards on this keypoint, one entry per reward row."""
    out: list[dict] = []
    for lane, slot in iter_slot_pairs(kp):
        for reward in slot.get("rewards") or []:
            if not isinstance(reward, dict) or not is_progress_reward(reward):
                continue
            taken = None
            for k in ("taken", "claimed", "rewardTaken", "completed", "granted"):
                if k in slot and slot[k] is not None:
                    taken = bool(slot[k])
                    break
                if k in reward and reward[k] is not None:
                    taken = bool(reward[k])
                    break
            out.append({
                "lane": lane,
                "code": reward_code(reward),
                "amount": reward_amount(reward),
                "taken": taken,
            })
    return out


def summarize_rewards(op: dict, *, cleared_only: bool = False) -> dict[str, float]:
    """Sum slot reward amounts. Optionally only keypoints already cleared."""
    from .buff_cache import keypoint_status, keypoints_of, stages_of, unwrap_operation

    op = unwrap_operation(op)
    totals: dict[str, float] = defaultdict(float)
    for st in stages_of(op):
        for kp in keypoints_of(st):
            if cleared_only and keypoint_status(kp) != "cleared":
                continue
            for _lane, slot in iter_slot_pairs(kp):
                for reward in slot.get("rewards") or []:
                    if not isinstance(reward, dict):
                        continue
                    code = reward_code(reward)
                    if not code:
                        continue
                    totals[code] += reward_amount(reward)
    return dict(totals)


def format_reward_line(totals: dict[str, float], *, limit: int = 6) -> str:
    if not totals:
        return "无"
    parts = []
    items = sorted(totals.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    for code, amount in items[:limit]:
        label = CODE_ZH.get(code, CODE_ZH.get(code.lower(), code))
        if abs(amount - int(amount)) < 1e-6:
            parts.append(f"{label} ×{int(amount)}")
        else:
            parts.append(f"{label} ×{amount:g}")
    extra = len(items) - limit
    if extra > 0:
        parts.append(f"其他 {extra} 项")
    return " · ".join(parts)


def classify_progress_outcome(
    rewards: list[dict],
    *,
    keypoint_cleared: bool,
    solution: dict | None,
) -> dict[str, Any]:
    """Count progress-token rewards fully obtained vs missed after a battle.

    Explicit ``taken`` on the slot wins. Otherwise a cleared keypoint counts a
    lane as fully obtained when that lane was a certain / 100% win; a keypoint
    that did not clear counts every progress reward as missed.
    """
    full_n = 0
    miss_n = 0
    points_full = 0.0
    points_miss = 0.0
    slots = (solution or {}).get("slots") or []
    for reward in rewards:
        lane = int(reward.get("lane") or 0)
        amount = float(reward.get("amount") or 0)
        taken = reward.get("taken")
        got: bool
        if taken is not None:
            got = bool(taken)
        elif not keypoint_cleared:
            got = False
        else:
            slot = slots[lane] if lane < len(slots) else {}
            status = str(slot.get("status") or "")
            try:
                wp = float(slot.get("win_pct") or 0)
            except (TypeError, ValueError):
                wp = 0.0
            got = status == "certain" or wp >= 99.95 or bool((solution or {}).get("all_certain"))
        if got:
            full_n += 1
            points_full += amount
        else:
            miss_n += 1
            points_miss += amount
    return {
        "full": full_n,
        "missed": miss_n,
        "points_full": points_full,
        "points_missed": points_miss,
    }
