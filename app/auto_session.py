"""Background 自动化窝牌 loop.

Places cards and clicks 战斗 only. It does not buy, post, or change account
settings — those stay on the player. Battle start is a click of the on-page
「战斗」 button (the inject script still never calls keypoint/start).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.auto_plan import (
    LAYER_ZH,
    AutoCancelled,
    cheap_transition_plan,
    finalize_reason,
    find_keypoint,
    format_hms,
    layered_battle_plan,
    lobby_progress,
    next_operation_id,
    select_target,
)
from core.buff_cache import BuffCache, unwrap_operation
from core.resource_summary import (
    classify_progress_outcome,
    format_reward_line,
    iter_slot_pairs,
    progress_rewards_on_keypoint,
    reward_amount,
    reward_code,
)

from .logutil import get_logger
from .worker import build_scenario_from_snapshot, solution_to_deploy_slots

log = get_logger("auto")


def campaign_ready(cache: BuffCache) -> bool:
    if not cache.stages:
        return False
    if cache.is_complete:
        return True
    return all(bool(st.goal.get("met")) for st in cache.stages) and select_target(cache) is None


class AutoSession(QObject):
    progress = Signal(dict)
    solve_progress = Signal(str, int)
    finished_summary = Signal(dict)
    status = Signal(str)

    def __init__(self, bridge, settings):
        super().__init__()
        self._bridge = bridge
        self._settings = settings
        self._lock = threading.Lock()
        self._cancel = False
        self._t0 = 0.0
        self._thread: threading.Thread | None = None
        self._snapshot: dict | None = None
        self._snap_ev = threading.Event()
        self._deploy: dict | None = None
        self._deploy_ev = threading.Event()
        self._auto_msg: dict | None = None
        self._auto_ev = threading.Event()
        self.active = False
        self.cache = BuffCache()

    def elapsed(self) -> float:
        with self._lock:
            t0 = self._t0
        if not t0:
            return 0.0
        return max(0.0, time.monotonic() - t0)

    def start(self) -> bool:
        if self.active:
            return False
        self._cancel = False
        self.active = True
        with self._lock:
            self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="pf-auto", daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel = True
        self._snap_ev.set()
        self._deploy_ev.set()
        self._auto_ev.set()

    def notify_snapshot(self, data: dict) -> None:
        with self._lock:
            self._snapshot = data
        self._snap_ev.set()

    def notify_deploy(self, data: dict) -> None:
        with self._lock:
            self._deploy = data
        self._deploy_ev.set()

    def notify_auto(self, data: dict) -> None:
        with self._lock:
            self._auto_msg = data
        self._auto_ev.set()

    # ----- waits -----
    def _stopped(self) -> bool:
        return bool(self._cancel)

    def _refresh(self, timeout: float = 20.0) -> dict | None:
        self._snap_ev.clear()
        if not self._bridge or not self._bridge.request_scrape():
            return None
        return self._wait_snap(timeout)

    def _wait_snap(self, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopped():
                return None
            if not self._snap_ev.wait(0.2):
                continue
            with self._lock:
                snap = self._snapshot
            self._snap_ev.clear()
            if isinstance(snap, dict):
                return snap
        return None

    def _wait_deploy(self, timeout: float = 20.0) -> dict | None:
        self._deploy_ev.clear()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopped():
                return None
            if not self._deploy_ev.wait(0.2):
                continue
            with self._lock:
                data = self._deploy
            self._deploy_ev.clear()
            if isinstance(data, dict):
                return data
        return None

    def _wait_auto(self, action: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopped():
                return None
            if not self._auto_ev.wait(0.2):
                continue
            with self._lock:
                msg = self._auto_msg
                self._auto_msg = None
            self._auto_ev.clear()
            if isinstance(msg, dict) and msg.get("action") == action:
                return msg
        return None

    def _emit(self, **info) -> None:
        self.progress.emit(info)

    def _run(self) -> None:
        summary: dict[str, Any] = {
            "reason": "中断",
            "elapsed_sec": 0.0,
            "battles": 0,
            "operation_stats": [],
            "resources": {},
            "resource_line": "无",
            "notes": [],
        }
        stats: dict[str, dict] = {}
        resources: dict[str, float] = {}
        completed = False
        try:
            summary = self._loop(summary, stats, resources)
            completed = summary.get("reason") == "完成"
        except AutoCancelled:
            summary["reason"] = "用户终止"
            summary["notes"].append("已按停止计算中止自动化")
        except Exception as e:
            log.exception("auto session")
            summary["notes"].append(f"自动化中断：{e}")
            summary["reason"] = "中断"
        if self._stopped() and summary.get("reason") != "完成":
            summary["reason"] = finalize_reason(cancelled=True, completed=False)
        elif completed:
            summary["reason"] = "完成"
        summary["elapsed_sec"] = self.elapsed()
        summary["operation_stats"] = list(stats.values())
        summary["resources"] = resources
        summary["resource_line"] = format_reward_line(resources)
        log.info(
            "auto end reason=%s battles=%s elapsed=%s",
            summary["reason"],
            summary["battles"],
            format_hms(summary["elapsed_sec"]),
        )
        self.finished_summary.emit(summary)

    def _touch_stat(self, stats: dict, cache: BuffCache) -> dict:
        sid = cache.operation_id or "operation"
        row = stats.get(sid)
        if row is None:
            row = {
                "id": sid,
                "name": cache.operation_name or sid,
                "full": 0,
                "missed": 0,
                "points_full": 0.0,
                "points_missed": 0.0,
                "battles": 0,
                "cleared": cache.cleared_count,
                "total": cache.total_count,
            }
            stats[sid] = row
        row["name"] = cache.operation_name or row["name"]
        row["cleared"] = cache.cleared_count
        row["total"] = cache.total_count
        return row

    def _loop(self, summary: dict, stats: dict, resources: dict) -> dict:
        self.status.emit("自动化窝牌：刷新行动…")
        snap = self._refresh(20)
        if self._stopped():
            summary["reason"] = "用户终止"
            return summary
        if not snap:
            summary["notes"].append("页面未连接或采数超时。请先注入脚本并停在行动页。")
            summary["reason"] = "中断"
            return summary
        self.cache = BuffCache()
        guard = 0
        while not self._stopped():
            guard += 1
            if guard > 80:
                summary["notes"].append("连续推进次数过多，已停下以免空转。")
                summary["reason"] = "中断"
                return summary
            op = unwrap_operation(snap.get("operation") if isinstance(snap, dict) else None)
            if not op or not op.get("stages"):
                summary["notes"].append("快照里没有行动数据。请打开某个行动的据点页后再试。")
                summary["reason"] = "中断"
                return summary
            diff = self.cache.update_from_operation(op)
            log.info(
                "buff cache op=%s recomputed=%s unchanged=%s",
                self.cache.operation_id,
                len(diff.get("recomputed") or []),
                len(diff.get("unchanged") or []),
            )
            lobby_ops = _lobby_sources(snap, op)
            lobby = lobby_progress(self.cache, lobby_ops)
            stat = self._touch_stat(stats, self.cache)
            target = select_target(self.cache)
            blocked = campaign_note_blocked(self.cache)
            if target is None:
                if campaign_ready(self.cache):
                    nxt = next_operation_id(lobby, self.cache.operation_id)
                    if nxt:
                        self._emit(
                            name="切换下一行动",
                            enemy_power=None,
                            reason=nxt,
                            step="本行动阶段目标已完成，读取下一行动",
                            lobby=lobby,
                            token_full=stat["full"],
                            token_missed=stat["missed"],
                            stage_goal={},
                        )
                        loaded = self._fetch_operation(nxt)
                        if not loaded:
                            summary["notes"].append(f"无法读取下一行动 {nxt}")
                            summary["reason"] = "中断"
                            return summary
                        snap = dict(snap)
                        snap["operation"] = loaded
                        continue
                    summary["reason"] = "完成"
                    summary["notes"].append("当前可见行动已打完。")
                    if lobby.get("uncertain"):
                        summary["notes"].append("大厅列表不完整时，按三个行动计进度；未能自动跳到未出现在列表里的行动。")
                    return summary
                summary["notes"].append(blocked or "没有可攻击的据点，自动化停下。")
                summary["reason"] = "中断"
                return summary

            self._emit(
                name=target["name"],
                enemy_power=target["enemy_power"],
                reason=target["reason"],
                step="准备计算" if not target["transition"] else "过渡战斗：跳过穷尽计算",
                lobby=lobby,
                token_full=stat["full"],
                token_missed=stat["missed"],
                stage_goal=target.get("stage_goal") or {},
                keypoint_id=target["keypoint_id"],
            )
            snap = self._ensure_keypoint(snap, op, target["keypoint_id"])
            if self._stopped():
                summary["reason"] = "用户终止"
                return summary
            if snap is None:
                summary["notes"].append(
                    f"无法切到据点 {target['name']}。SPA 跳转失败时需要你手动点开该据点；"
                    "若页面整页刷新，请在 F12 重新粘贴注入脚本。"
                )
                summary["reason"] = "中断"
                return summary
            op = unwrap_operation(snap.get("operation"))
            self.cache.update_from_operation(op)
            try:
                scenario = build_scenario_from_snapshot(snap)
            except Exception as e:
                summary["notes"].append(f"采数解析失败：{e}")
                summary["reason"] = "中断"
                return summary
            if not scenario.get("enemies") or not scenario.get("ships"):
                summary["notes"].append("据点场景不完整（缺敌方或舰船），无法放牌。")
                summary["reason"] = "中断"
                return summary

            eff_lanes = list(target["progress_lanes"]) if target.get("is_last") else []
            try:
                if target["transition"]:
                    solution = cheap_transition_plan(scenario)
                    layer = "transition"
                    self.solve_progress.emit("过渡战斗：一张最低战力牌", 100)
                else:
                    allowed = list(getattr(self._settings, "enabled_combat_mods", None) or [])
                    plan = layered_battle_plan(
                        scenario,
                        top_n=50,
                        progress_lanes=eff_lanes,
                        attach_effectiveness_lanes=eff_lanes,
                        allowed_mods=allowed,
                        snapshot=snap,
                        cancel=lambda: self._cancel,
                        progress=lambda msg, pct: self.solve_progress.emit(str(msg), int(pct)),
                    )
                    solution = plan["solution"]
                    layer = plan["layer"]
            except AutoCancelled:
                raise
            except Exception as e:
                log.exception("auto solve")
                summary["notes"].append(f"{target['name']} 计算失败：{e}")
                summary["reason"] = "中断"
                return summary

            slots = solution_to_deploy_slots(solution, snap)
            filled = sum(1 for s in slots if s)
            if filled <= 0:
                summary["notes"].append(f"{target['name']} 没有可放的舰船。")
                summary["reason"] = "中断"
                return summary
            self._emit(
                name=target["name"],
                enemy_power=target["enemy_power"],
                reason=target["reason"],
                step=f"放牌（{LAYER_ZH.get(layer, layer)}）· {filled} 路",
                lobby=lobby_progress(self.cache, _lobby_sources(snap, op)),
                token_full=stat["full"],
                token_missed=stat["missed"],
                stage_goal=target.get("stage_goal") or {},
            )
            if not self._bridge.request_deploy(slots, clear=True):
                summary["notes"].append("放牌指令没有送到页面。")
                summary["reason"] = "中断"
                return summary
            deployed = self._wait_deploy(20)
            if self._stopped():
                summary["reason"] = "用户终止"
                return summary
            if not deployed or not deployed.get("ok"):
                err = ""
                if isinstance(deployed, dict):
                    detail = deployed.get("detail") if isinstance(deployed.get("detail"), dict) else {}
                    err = str(detail.get("error") or deployed.get("error") or "")
                summary["notes"].append("放牌失败" + (f"：{err}" if err else ""))
                summary["reason"] = "中断"
                return summary
            time.sleep(0.35)
            if self._stopped():
                summary["reason"] = "用户终止"
                return summary
            self._auto_ev.clear()
            if not self._bridge.request_start_battle():
                summary["notes"].append("开战指令没有送到页面。")
                summary["reason"] = "中断"
                return summary
            started = self._wait_auto("start_battle", 15)
            if self._stopped():
                summary["reason"] = "用户终止"
                return summary
            if not started or not started.get("ok"):
                err = (started or {}).get("error") or "没有点到「战斗」按钮"
                summary["notes"].append(
                    f"未能开战：{err}。自动化只点文案为「战斗」的按钮，不会点购买、发帖或设置。"
                )
                summary["reason"] = "中断"
                return summary
            self._emit(
                name=target["name"],
                enemy_power=target["enemy_power"],
                reason=target["reason"],
                step="已开战，等待结算",
                lobby=lobby_progress(self.cache, _lobby_sources(snap, op)),
                token_full=stat["full"],
                token_missed=stat["missed"],
                stage_goal=target.get("stage_goal") or {},
            )
            after = self._wait_cleared(target["keypoint_id"], timeout=120)
            if self._stopped():
                summary["reason"] = "用户终止"
                return summary
            if after is None:
                if target.get("in_last_stage"):
                    outcome = classify_progress_outcome(
                        target.get("progress_rewards") or [],
                        keypoint_cleared=False,
                        solution=solution,
                    )
                    stat["full"] += outcome["full"]
                    stat["missed"] += outcome["missed"]
                    stat["points_full"] += outcome["points_full"]
                    stat["points_missed"] += outcome["points_missed"]
                summary["notes"].append(f"{target['name']} 开战后面板没有在时限内变成已清除。")
                summary["reason"] = "中断"
                return summary
            snap = after
            op = unwrap_operation(snap.get("operation"))
            self.cache.update_from_operation(op)
            cleared = False
            row = self.cache.row(target["keypoint_id"])
            if row is not None:
                cleared = row.status == "cleared"
            kp_after = find_keypoint(op, target["keypoint_id"]) or {}
            if target.get("in_last_stage"):
                rewards = progress_rewards_on_keypoint(kp_after) or target.get("progress_rewards") or []
                # Prefer live taken-flags from the post-battle payload.
                outcome = classify_progress_outcome(
                    rewards,
                    keypoint_cleared=cleared,
                    solution=solution,
                )
                stat["full"] += outcome["full"]
                stat["missed"] += outcome["missed"]
                stat["points_full"] += outcome["points_full"]
                stat["points_missed"] += outcome["points_missed"]
            if cleared:
                _add_keypoint_rewards(resources, kp_after)
            stat["battles"] += 1
            summary["battles"] += 1
            self._touch_stat(stats, self.cache)
            goal = {}
            if row is not None:
                for st in self.cache.stages:
                    if st.index == row.stage_index:
                        goal = st.goal
                        break
            rem = goal.get("remaining")
            self.status.emit(
                f"已结算 {target['name']}"
                + (f" · 阶段还差 {int(rem)}" if rem is not None else "")
            )
        summary["reason"] = "用户终止"
        return summary

    def _ensure_keypoint(self, snap: dict, op: dict, kp_id: str) -> dict | None:
        current = _snap_kp(snap)
        if current == kp_id and _scenario_ready(snap):
            return snap
        op_id = str(op.get("id") or self.cache.operation_id or "")
        self._auto_ev.clear()
        if not self._bridge.request_open_keypoint(op_id, kp_id):
            return None
        opened = self._wait_auto("open_keypoint", 12)
        if self._stopped():
            return None
        if not opened or not opened.get("ok"):
            log.info("open_keypoint failed: %s", opened)
            return None
        time.sleep(0.6)
        for _ in range(4):
            if self._stopped():
                return None
            fresh = self._refresh(15)
            if not fresh:
                continue
            if _snap_kp(fresh) == kp_id:
                return fresh
            # History push may not move the SPA; accept a scrape whose operation
            # keypoint matches if meta was updated by the page.
            snap = fresh
        if _snap_kp(snap) == kp_id:
            return snap
        return None

    def _fetch_operation(self, op_id: str) -> dict | None:
        self._auto_ev.clear()
        if not self._bridge.request_fetch_operation(op_id):
            return None
        msg = self._wait_auto("fetch_operation", 20)
        if not msg or not msg.get("ok"):
            return None
        op = unwrap_operation(msg.get("operation") if isinstance(msg.get("operation"), dict) else None)
        if not op.get("stages"):
            return None
        return op

    def _wait_cleared(self, kp_id: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopped():
                return None
            time.sleep(2.5)
            snap = self._refresh(15)
            if not snap:
                continue
            op = unwrap_operation(snap.get("operation"))
            if not op:
                continue
            # Incremental: only rows whose effects/status changed are recomputed.
            self.cache.update_from_operation(op)
            row = self.cache.row(kp_id)
            if row is not None and row.status == "cleared":
                return snap
            kp = find_keypoint(op, kp_id)
            if kp is not None:
                from core.buff_cache import keypoint_status

                if keypoint_status(kp) == "cleared":
                    return snap
        return None


def campaign_note_blocked(cache: BuffCache) -> str | None:
    if any(not st.goal.get("met") for st in cache.stages):
        if not any(r.status == "available" for r in cache.iter_rows()):
            return "还有阶段目标未完成，但没有可攻击的据点"
    return None


def _snap_kp(snap: dict | None) -> str | None:
    if not isinstance(snap, dict):
        return None
    meta = snap.get("meta") or {}
    if meta.get("keypointId"):
        return str(meta["keypointId"])
    kp = unwrap_operation(snap.get("keypoint")) if isinstance(snap.get("keypoint"), dict) else None
    # keypoint payloads are not operations; unwrap only strips {result}.
    if isinstance(snap.get("keypoint"), dict):
        raw = snap["keypoint"]
        inner = raw.get("result") if isinstance(raw.get("result"), dict) else raw
        if isinstance(inner, dict) and inner.get("id"):
            return str(inner["id"])
    return None


def _scenario_ready(snap: dict) -> bool:
    try:
        sc = build_scenario_from_snapshot(snap)
    except Exception:
        return False
    return bool(sc.get("enemies") and sc.get("ships"))


def _lobby_sources(snap: dict, op: dict) -> list[dict] | None:
    for src in (snap, op):
        if not isinstance(src, dict):
            continue
        for key in ("operations", "lobbyOperations", "lobby_operations"):
            raw = src.get(key)
            if isinstance(raw, list) and raw:
                return raw
    return None


def _add_keypoint_rewards(resources: dict[str, float], kp: dict) -> None:
    for _lane, slot in iter_slot_pairs(kp or {}):
        for reward in slot.get("rewards") or []:
            if not isinstance(reward, dict):
                continue
            code = reward_code(reward)
            if not code:
                continue
            resources[code] = float(resources.get(code) or 0) + reward_amount(reward)
