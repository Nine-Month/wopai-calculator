#!/usr/bin/env python3
"""Pure-function tests for 自动化窝牌 selection, stage goals, and layered solves.

Loads data/today_op2_operation.json and data/mike_operation.json (synthetic
operation payloads in the live API shape). No game client required.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.auto_plan import (  # noqa: E402
    AUTO_TOP_N,
    apply_purchasable_best_stat,
    attach_effectiveness,
    cheap_transition_plan,
    finalize_reason,
    layered_battle_plan,
    lobby_progress,
    next_operation_id,
    pick_operation_to_enter,
    progress_bar_text,
    select_target,
)
from core.buff_cache import BuffCache, is_harmful_effect, keypoint_status, stage_destroy_goal  # noqa: E402
from core.items_solve import UNPURCHASABLE_COMBAT, empty_mods  # noqa: E402
from core.resource_summary import (  # noqa: E402
    classify_progress_outcome,
    is_progress_reward,
    progress_rewards_on_keypoint,
)
from core.solver import parse_scenario  # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _load(name: str) -> dict:
    path = ROOT / "data" / name
    _assert(path.is_file(), f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _ship(code, fp, surv, mob, effects=None):
    return {
        "name": code,
        "code": code,
        "firepower": fp,
        "survivability": surv,
        "mobility": mob,
        "hp": 3,
        "effects": effects or [],
    }


def _enemy(name, power):
    return {"name": name, "power": power, "weaknesses": [], "reward": 0, "code": name}


def _scenario(ships, enemies):
    return {"ships": ships, "enemies": enemies, "meta": {}}


def test_today_priority_and_incremental():
    op = _load("today_op2_operation.json")
    cache = BuffCache.from_operation(op)
    art = cache.row("kp-artillery")
    weak = cache.row("kp-weak")
    done = cache.row("kp-done")
    _assert(art and weak and done, "rows missing")
    _assert(abs(art.enemy_power - 400) < 0.05, f"art power {art.enemy_power}")
    _assert(abs(weak.bare_power - 100) < 0.05, f"weak bare {weak.bare_power}")
    _assert(abs(weak.enemy_power - 116) < 0.05, f"weak buffed {weak.enemy_power}")
    _assert(art.cuts_harm and not weak.cuts_harm, "artillery should be the harm source")
    goal = cache.stages[0].goal
    _assert(goal["type"] == "cleared_stage", goal)
    _assert(goal["current"] == 1 and goal["target"] == 2 and goal["remaining"] == 1, goal)
    _assert(not goal["met"], goal)
    picked = select_target(cache)
    _assert(picked["keypoint_id"] == "kp-artillery", picked)
    _assert(not picked["transition"], picked)
    _assert("削减" in picked["reason"], picked["reason"])

    # Clear the battery and drop its region buff. Weak board power must fall;
    # the already-cleared node with an unchanged effect signature stays cached.
    nxt = copy.deepcopy(op)
    for kp in nxt["stages"][0]["keypoints"]:
        if kp["id"] == "kp-artillery":
            kp["status"] = "cleared"
    nxt["effects"] = []
    diff = cache.update_from_operation(nxt)
    _assert("kp-weak" in diff["recomputed"], diff)
    _assert("kp-done" in diff["unchanged"], diff)
    _assert("kp-artillery" in diff["unchanged"], diff)
    _assert("kp-artillery" in diff["status_changed"], diff)
    weak2 = cache.row("kp-weak")
    _assert(abs(weak2.enemy_power - 100) < 0.05, weak2.enemy_power)
    _assert(abs(cache.row("kp-artillery").enemy_power - 400) < 0.05, "art power recomputed")
    goal2 = cache.stages[0].goal
    _assert(goal2["met"] and goal2["remaining"] == 0, goal2)
    nxt_pick = select_target(cache)
    _assert(nxt_pick["keypoint_id"] == "kp-weak", nxt_pick)
    _assert(nxt_pick["transition"], nxt_pick)
    final = cache.row("kp-final")
    _assert(final.is_last and final.progress_lanes == [0, 1], final.as_dict())
    lobby = lobby_progress(cache, op["lobbyOperations"])
    _assert(lobby["ops_done"] == 0 and lobby["ops_total"] == 3, lobby)
    _assert(lobby["cleared_kp"] == 2 and lobby["total_kp"] == 4, lobby)
    _assert(progress_bar_text(lobby) == "行动 0/3 · 据点 2/4", progress_bar_text(lobby))


def test_mike_resume():
    op = _load("mike_operation.json")
    cache = BuffCache.from_operation(op)
    _assert(cache.cleared_count == 6 and cache.total_count == 8, (cache.cleared_count, cache.total_count))
    _assert(cache.stages[1].goal["remaining"] == 2, cache.stages[1].goal)
    picked = select_target(cache)
    _assert(picked["keypoint_id"] == "mike-b3", picked)
    _assert(not picked["transition"], picked)
    _assert(abs(picked["enemy_power"] - 90) < 0.05, picked)
    lobby = lobby_progress(cache, op["lobbyOperations"])
    _assert(lobby["ops_done"] == 1 and lobby["ops_total"] == 3, lobby)
    _assert(not lobby["current_complete"], lobby)
    _assert(progress_bar_text(lobby) == "行动 1/3 · 据点 6/8", progress_bar_text(lobby))


def test_player_debuff_priority():
    op = {
        "id": "op-debuff",
        "name": "减益",
        "stages": [{
            "id": "s",
            "condition": {"type": "cleared_stage", "target": 1, "total": 2},
            "keypoints": [
                {
                    "id": "low",
                    "name": "低战力",
                    "status": "available",
                    "slots": {"0": {"enemy": {"code": "e1", "name": "e", "power": 40, "vulns": {}}, "rewards": []}},
                },
                {
                    "id": "aura",
                    "name": "削弱光环",
                    "status": "available",
                    "slots": {"0": {"enemy": {"code": "e2", "name": "e", "power": 300, "vulns": {}}, "rewards": []}},
                },
            ],
        }],
        "effects": [{
            "source": "aura",
            "applyTo": ["low"],
            "payload": {"type": 1, "specs": {"0": -0.2, "1": -0.2, "2": -0.2}},
        }],
    }
    _assert(is_harmful_effect(op["effects"][0]), "negative region specs are a player debuff")
    _assert(is_harmful_effect({
        "payload": {"type": 2, "multiplier": 0},
    }), "buff multiplier 0 is our debuff")
    cache = BuffCache.from_operation(op)
    picked = select_target(cache)
    _assert(picked["keypoint_id"] == "aura", picked)


def test_harm_source_not_cheap_after_quota():
    """Quota can be met while a buff source is still up — still full-clear it."""
    op = {
        "id": "op-quota",
        "name": "配额已满但仍有光环",
        "stages": [{
            "id": "s",
            "condition": {"type": "cleared_stage", "target": 1, "total": 3, "current": 1},
            "keypoints": [
                {"id": "done", "name": "已清", "status": "cleared",
                 "slots": {"0": {"enemy": {"code": "e0", "name": "e", "power": 10, "vulns": {}}, "rewards": []}}},
                {"id": "aura", "name": "光环", "status": "available",
                 "slots": {"0": {"enemy": {"code": "e1", "name": "e", "power": 500, "vulns": {}}, "rewards": []}}},
                {"id": "weak", "name": "弱", "status": "available",
                 "slots": {"0": {"enemy": {"code": "e2", "name": "e", "power": 30, "vulns": {}}, "rewards": []}}},
            ],
        }],
        "effects": [{
            "source": "aura",
            "applyTo": ["weak"],
            "payload": {"type": 4, "specs": {"0": 0.2}},
        }],
    }
    cache = BuffCache.from_operation(op)
    _assert(cache.stages[0].goal["met"], cache.stages[0].goal)
    picked = select_target(cache)
    _assert(picked["keypoint_id"] == "aura", picked)
    _assert(not picked["transition"], picked)


def test_stage_goal_explicit_current():
    stage = {
        "condition": {"type": "cleared_stage", "target": 5, "total": 8, "current": 3},
        "keypoints": [{"id": "a", "status": "cleared"}, {"id": "b", "status": "available"}],
    }
    goal = stage_destroy_goal(stage)
    _assert(goal["remaining"] == 2 and not goal["met"], goal)


def test_layered_lowest_overflow():
    buff = [{"kind": "buff", "direction": "right", "type": "firepower", "value": 0.3}]
    ships = [
        _ship("card_buff", 40, 40, 40, buff),
        _ship("card_b", 40, 40, 40),
        _ship("card_c", 40, 40, 40),
        _ship("card_d", 40, 40, 40),
    ]
    enemies = [_enemy(f"e{i}", 50) for i in range(4)]
    plan = layered_battle_plan(_scenario(ships, enemies), top_n=5)
    _assert(plan["layer"] == "no_item", plan["layer"])
    _assert(plan["full_win"], plan)
    sol = plan["solution"]
    _assert(abs(sol["total_overflow"] - 240) < 0.2, sol["total_overflow"])
    _assert(sol["ship_codes"][-1] == "card_buff", sol["ship_codes"])


def test_layered_purchasable_best_stat_only():
    ships = [_ship(f"card_{i}", 80, 15, 10) for i in range(4)]
    enemies = [_enemy(f"e{i}", 100) for i in range(4)]
    plan = layered_battle_plan(_scenario(ships, enemies), top_n=2)
    _assert(plan["layer"] == "purchasable", plan["layer"])
    _assert(plan["full_win"], plan)
    for mods in plan["solution"]["lane_mods"]:
        _assert(mods == ["gunners-training"], mods)
        _assert("teamwork" not in mods, mods)


def test_progress_lane_skips_purchasable_training():
    ships, enemies, _meta = parse_scenario(_scenario(
        [_ship(f"card_{i}", 80, 15, 10) for i in range(4)],
        [_enemy(f"e{i}", 100) for i in range(4)],
    ))
    from core.auto_plan import build_combat_catalog
    from core.solver import filter_pool

    catalog, owned = build_combat_catalog()
    pool = filter_pool(ships)
    base = empty_mods(4)
    ev = {
        "slots": [{"status": "band"} for _ in range(4)],
    }
    hit = apply_purchasable_best_stat(
        pool,
        base,
        ev,
        enemies,
        catalog,
        owned,
        skip_lanes={0},
        boss_lane=0,
    )
    mods = hit["lane_mods"]
    _assert("gunners-training" not in (mods[0] or []), mods)
    for lane in mods[1:]:
        _assert(lane == ["gunners-training"], mods)
    _assert(all("teamwork" not in (lane or []) for lane in mods), mods)


def test_last_keypoint_effectiveness():
    buff = [{"kind": "buff", "direction": "right", "type": "firepower", "value": 0.3}]
    ships = [
        _ship("card_buff", 40, 40, 40, buff),
        _ship("card_b", 40, 40, 40),
        _ship("card_c", 40, 40, 40),
        _ship("card_d", 40, 40, 40),
    ]
    enemies = [_enemy(f"e{i}", 50) for i in range(4)]
    plan = layered_battle_plan(
        _scenario(ships, enemies),
        top_n=4,
        progress_lanes=[0, 1],
        attach_effectiveness_lanes=[0, 1],
    )
    _assert(plan["layer"] == "no_item", plan["layer"])
    mods = plan["solution"]["lane_mods"]
    _assert("effectiveness" in mods[0] and "effectiveness" in mods[1], mods)
    _assert("effectiveness" not in mods[2] and "effectiveness" not in mods[3], mods)
    _assert("gunners-training" not in mods[0], mods)
    # Direct attach replaces a training common.
    sol = attach_effectiveness(
        {"slots": [{"status": "certain", "ship_code": "card_b", "ship": "b"}], "lane_mods": [["gunners-training", "focused"]]},
        [0],
    )
    _assert(sol["lane_mods"][0] == ["focused", "effectiveness"], sol["lane_mods"])


def test_unpurchasable_then_fallback():
    ships = [_ship(f"card_{i}", 30, 30, 30) for i in range(4)]
    enemies = [_enemy("hard", 100), _enemy("a", 40), _enemy("b", 40), _enemy("c", 40)]
    # Rares are not for sale — the ladder may place them only when owned.
    owned = {mid: 4 for mid in UNPURCHASABLE_COMBAT}
    plan = layered_battle_plan(_scenario(ships, enemies), top_n=2, owned=owned)
    _assert(plan["layer"] == "unpurchasable", plan["layer"])
    _assert(plan["full_win"], plan)
    hard_mods = plan["solution"]["lane_mods"][0]
    _assert("focused" in hard_mods, plan["solution"]["lane_mods"])

    weak = [_ship(f"card_{i}", 20, 20, 20) for i in range(4)]
    hard_enemies = [_enemy(f"e{i}", 100) for i in range(4)]
    fallback = layered_battle_plan(_scenario(weak, hard_enemies), top_n=2)
    _assert(fallback["layer"] == "purchasable_fallback", fallback["layer"])
    _assert(not fallback["full_win"], fallback)
    _assert(fallback["solution"].get("ship_codes"), fallback["solution"])
    # Same hard node, but rares are not owned: cannot spend them, so no full win.
    unowned = layered_battle_plan(_scenario(ships, enemies), top_n=2, owned={})
    _assert(unowned["layer"] == "purchasable_fallback", unowned["layer"])
    _assert("focused" not in str(unowned["solution"].get("lane_mods")), unowned["solution"].get("lane_mods"))


def test_cheap_and_tokens():
    ships = [_ship("card_big", 50, 50, 50), _ship("card_tiny", 5, 5, 5)]
    enemies = [_enemy("e0", 200), _enemy("e1", 200)]
    sol = cheap_transition_plan(_scenario(ships, enemies))
    filled = [s for s in sol["slots"] if s.get("status") != "empty"]
    _assert(len(filled) == 1, sol["slots"])
    _assert(filled[0].get("ship_code") == "card_tiny", filled)
    _assert(finalize_reason(cancelled=True, completed=False) == "用户终止", finalize_reason(cancelled=True, completed=False))
    _assert(finalize_reason(cancelled=False, completed=True) == "完成", "complete reason")
    _assert(is_progress_reward({"code": "adeck_progress_points", "amount": 10}), "progress code")
    kp = _load("today_op2_operation.json")["stages"][1]["keypoints"][0]
    rewards = progress_rewards_on_keypoint(kp)
    _assert(len(rewards) == 2, rewards)
    missed = classify_progress_outcome(rewards, keypoint_cleared=False, solution=None)
    _assert(missed["full"] == 0 and missed["missed"] == 2, missed)
    certain = {
        "all_certain": True,
        "slots": [{"status": "certain"}, {"status": "certain"}],
    }
    got = classify_progress_outcome(rewards, keypoint_cleared=True, solution=certain)
    _assert(got["full"] == 2 and got["points_full"] == 50, got)
    _assert(AUTO_TOP_N == 50, AUTO_TOP_N)


def test_unrevealed_not_selectable():
    """CHARLIE opening: buff source kp_04 is unrevealed, so it is not a target."""
    _assert(keypoint_status({"status": "revealed"}) == "available", "revealed")
    _assert(keypoint_status({"status": "unrevealed"}) == "locked", "unrevealed")
    _assert(keypoint_status({}) == "locked", "unknown must not default to available")
    _assert(keypoint_status({"status": "not-a-real-state"}) == "locked", "unknown status")
    op = _load("charlie_opening.json")
    cache = BuffCache.from_operation(op)
    _assert(cache.row("kp_01").status == "available", cache.row("kp_01").status)
    _assert(cache.row("kp_02").status == "available", cache.row("kp_02").status)
    _assert(cache.row("kp_04").status == "locked", cache.row("kp_04").status)
    _assert(cache.row("kp_04").cuts_harm, "kp_04 is still the harmful source")
    _assert(not cache.row("kp_01").cuts_harm, "debuff victim is not the source")
    picked = select_target(cache)
    _assert(picked["keypoint_id"] != "kp_04", picked)
    _assert(picked["keypoint_id"] in {"kp_01", "kp_02"}, picked)
    _assert(picked["keypoint_id"] == "kp_02", picked)


def test_lobby_charlie_is_only_open():
    """Live hall: MIKE archived, CHARLIE open, 主力部队 archived → 2/3, enter CHARLIE.

    URL https://pacificfront.worldofwarships.asia/zh-sg/operations
    Left to right: strikethrough+ARCHIVE, open dossier, strikethrough+ARCHIVE.
    """
    op = _load("charlie_opening.json")
    hall = op["lobbyOperations"]
    empty = lobby_progress(BuffCache(), hall)
    _assert(empty["ops_done"] == 2 and empty["ops_total"] == 3, empty)
    _assert(pick_operation_to_enter(empty) == "charlie", empty)
    phases = {it["id"]: it["phase"] for it in empty["items"]}
    _assert(phases == {"mike": "completed", "charlie": "in_progress", "main-force": "completed"}, phases)
    cache = BuffCache.from_operation(op)
    lobby = lobby_progress(cache, hall)
    _assert(lobby["ops_done"] == 2 and lobby["ops_total"] == 3, lobby)
    _assert(lobby["cleared_kp"] == 0 and lobby["total_kp"] == 4, lobby)
    _assert(progress_bar_text(lobby) == "行动 2/3 · 据点 0/4", progress_bar_text(lobby))
    _assert(next_operation_id(lobby, "mike") == "charlie", lobby)
    _assert(next_operation_id(lobby, "charlie") is None, lobby)
    _assert(next_operation_id(lobby, "main-force") == "charlie", lobby)

    # Serial chain when nothing is archived yet: finish 1 → 2 → 3 → stop.
    chain = [
        {"id": "op1", "name": "一", "index": 0, "isFinished": False, "isStarted": True},
        {"id": "op2", "name": "二", "index": 1, "isFinished": False, "isStarted": False},
        {"id": "op3", "name": "三", "index": 2, "isFinished": False, "isStarted": False},
    ]

    def _done(op_id: str) -> BuffCache:
        return BuffCache.from_operation({
            "id": op_id,
            "name": op_id,
            "stages": [{
                "id": "s",
                "keypoints": [{
                    "id": "k",
                    "status": "cleared",
                    "slots": {"0": {"enemy": {"code": "e", "name": "e", "power": 10, "vulns": {}}, "rewards": []}},
                }],
            }],
        })

    first = lobby_progress(_done("op1"), [{**chain[0], "isFinished": True}, chain[1], chain[2]])
    _assert(first["ops_done"] == 1, first)
    _assert(next_operation_id(first, "op1") == "op2", first)
    second = lobby_progress(_done("op2"), [
        {**chain[0], "isFinished": True},
        {**chain[1], "isFinished": True},
        chain[2],
    ])
    _assert(next_operation_id(second, "op2") == "op3", second)
    third = lobby_progress(_done("op3"), [
        {**chain[0], "isFinished": True},
        {**chain[1], "isFinished": True},
        {**chain[2], "isFinished": True},
    ])
    _assert(third["ops_done"] == 3, third)
    _assert(next_operation_id(third, "op3") is None, third)
    _assert(pick_operation_to_enter(third) is None, third)


def test_wiring_and_inject():
    mw = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    _assert('self.btn_auto = QPushButton("自动化窝牌")' in mw, "auto button missing")
    _assert("self._auto_session.cancel()" in mw, "stop must cancel automation")
    _assert("AutoSummaryDialog" in mw, "summary dialog not shown")
    inject = (ROOT / "bridge" / "inject.js").read_text(encoding="utf-8")
    _assert("function startBattle()" in inject, "startBattle missing")
    _assert("keypoint/start" in inject, "inject should still document it does not call start API")
    _assert('msg.type === "start_battle"' in inject, "battle command not wired")
    _assert("PAY_OR_SETTINGS" in inject, "paid/settings guard missing")
    _assert("function openOperation(" in inject, "lobby enter missing")
    _assert("function returnToLobby(" in inject, "return to lobby missing")
    _assert('msg.type === "open_operation"' in inject, "open_operation command missing")
    _assert('msg.type === "return_lobby"' in inject, "return_lobby command missing")
    _assert("isFinished" in inject, "hall finished flag not read")
    _assert("location.reload(" not in inject, "inject must not full-reload")
    server = (ROOT / "app" / "bridge" / "server.py").read_text(encoding="utf-8")
    _assert("def request_open_operation" in server, "bridge open_operation missing")
    _assert("def request_return_lobby" in server, "bridge return_lobby missing")
    session = (ROOT / "app" / "auto_session.py").read_text(encoding="utf-8")
    _assert("pick_operation_to_enter" in session, "session does not enter from the hall")
    _assert("_return_lobby" in session, "session does not return to the hall")
    _assert("def request_start_battle" in server, "bridge start missing")
    # Manual deploy path must remain a place-only command.
    _assert('payload = {\n            "type": "deploy"' in server, "manual deploy changed")


def main():
    test_today_priority_and_incremental()
    test_mike_resume()
    test_player_debuff_priority()
    test_harm_source_not_cheap_after_quota()
    test_stage_goal_explicit_current()
    test_layered_lowest_overflow()
    test_layered_purchasable_best_stat_only()
    test_progress_lane_skips_purchasable_training()
    test_last_keypoint_effectiveness()
    test_unpurchasable_then_fallback()
    test_cheap_and_tokens()
    test_unrevealed_not_selectable()
    test_lobby_charlie_is_only_open()
    test_wiring_and_inject()
    print("AUTO PLAY TESTS OK")


if __name__ == "__main__":
    main()
