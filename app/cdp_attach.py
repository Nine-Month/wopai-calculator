"""Browser attach: prefer IE for default open; CDP/Chrome as fallback inject path."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logutil import get_logger
from .paths import app_dir

log = get_logger("cdp")

# Tab match heuristics (title or URL)
_TAB_KEYWORDS = (
    "太平洋前线",
    "太平洋前線",
    "pacific front",
    "pacificfront",
    "pacific-front",
    "pacificfront.worldofwarships",
    "worldofwarships.asia",
    "/operations/",
    "/keypoint",
    "/operation",
    "keypointId",
)

GAME_HOME_URL = "https://pacificfront.worldofwarships.asia/zh-sg/"

_DEFAULT_PORTS = list(range(9222, 9233))  # 9222..9232


@dataclass
class CdpTarget:
    port: int
    id: str
    title: str
    url: str
    ws_url: str


def _http_json(url: str, timeout: float = 1.5):
    req = urllib.request.Request(url, headers={"User-Agent": "PFHelper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def list_cdp_ports(ports: list[int] | None = None) -> list[int]:
    found = []
    for p in ports or _DEFAULT_PORTS:
        try:
            _http_json(f"http://127.0.0.1:{p}/json/version", timeout=0.4)
            found.append(p)
        except Exception:
            continue
    return found


def list_page_targets(port: int) -> list[CdpTarget]:
    try:
        raw = _http_json(f"http://127.0.0.1:{port}/json/list", timeout=1.5)
    except Exception as e:
        log.debug("list targets port %s failed: %s", port, e)
        return []
    out: list[CdpTarget] = []
    if not isinstance(raw, list):
        return out
    for t in raw:
        if not isinstance(t, dict):
            continue
        if t.get("type") not in (None, "page"):
            if t.get("type") != "page":
                continue
        ws = t.get("webSocketDebuggerUrl") or ""
        if not ws:
            continue
        out.append(
            CdpTarget(
                port=port,
                id=str(t.get("id") or ""),
                title=str(t.get("title") or ""),
                url=str(t.get("url") or ""),
                ws_url=ws,
            )
        )
    return out


def _score_target(t: CdpTarget) -> int:
    blob = (t.title + " " + t.url).lower()
    score = 0
    for kw in _TAB_KEYWORDS:
        if kw.lower() in blob:
            score += 10
    if t.url.startswith("http"):
        score += 1
    if t.url.startswith("chrome://") or t.url.startswith("edge://") or t.url.startswith("about:"):
        score -= 5
    return score


def find_best_target(ports: list[int] | None = None) -> CdpTarget | None:
    best: CdpTarget | None = None
    best_score = -999
    for port in list_cdp_ports(ports):
        for t in list_page_targets(port):
            s = _score_target(t)
            if s > best_score:
                best_score = s
                best = t
    if best is None:
        return None
    if best_score < 10:
        log.info("No keyword-matched tab; best was %r %r score=%s", best.title, best.url, best_score)
        return None
    return best


def _ie_candidates() -> list[Path]:
    """Internet Explorer paths (Windows)."""
    env = os.environ
    pf = env.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = [
        Path(pf86) / "Internet Explorer" / "iexplore.exe",
        Path(pf) / "Internet Explorer" / "iexplore.exe",
        Path(r"C:\Program Files\Internet Explorer\iexplore.exe"),
        Path(r"C:\Program Files (x86)\Internet Explorer\iexplore.exe"),
    ]
    out: list[Path] = []
    for p in candidates:
        if p.is_file() and p not in out:
            out.append(p)
    return out


def launch_ie(url: str = GAME_HOME_URL) -> Path | None:
    """Open game URL in Internet Explorer (default popup browser). No CDP."""
    if sys.platform != "win32":
        log.warning("launch_ie only supported on Windows")
        return None
    start_url = url or GAME_HOME_URL
    for exe in _ie_candidates():
        try:
            log.info("Launching IE: %s url=%s", exe, start_url)
            subprocess.Popen([str(exe), start_url], close_fds=True)
            return exe
        except Exception as e:
            log.warning("IE launch failed via %s: %s", exe, e)
    # Fallback: 'start iexplore URL' via cmd
    try:
        log.info("Launching IE via cmd start iexplore")
        subprocess.Popen(
            ["cmd", "/c", "start", "", "iexplore", start_url],
            close_fds=True,
            shell=False,
        )
        return Path("iexplore")
    except Exception as e:
        log.exception("IE cmd launch failed: %s", e)
        return None


def _browser_candidates() -> list[Path]:
    """Chrome/Edge paths — CDP fallback only (not default popup)."""
    paths: list[Path] = []
    env = os.environ
    local = env.get("LOCALAPPDATA", "")
    pf = env.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = [
        Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for p in candidates:
        if p.is_file():
            paths.append(p)
    return paths


def launch_debug_browser(port: int = 9222, url: str = GAME_HOME_URL) -> Path | None:
    """Launch Chrome/Edge with remote debugging (optional CDP fallback, not default)."""
    if sys.platform != "win32":
        log.warning("launch_debug_browser only supported on Windows")
        return None
    browsers = _browser_candidates()
    if not browsers:
        log.error("No Chrome/Edge found")
        return None
    exe = browsers[0]
    profile = app_dir() / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    start_url = url or GAME_HOME_URL
    args = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    log.info("Launching debug browser (CDP fallback): %s url=%s", args[:3], start_url)
    try:
        subprocess.Popen(args, close_fds=True)
    except Exception as e:
        log.exception("launch failed: %s", e)
        return None
    for _ in range(40):
        time.sleep(0.25)
        try:
            _http_json(f"http://127.0.0.1:{port}/json/version", timeout=0.5)
            log.info("CDP ready on %s", port)
            return exe
        except Exception:
            continue
    log.error("CDP did not come up on port %s", port)
    return None


async def _cdp_navigate(ws_url: str, url: str, timeout: float = 15.0) -> None:
    from aiohttp import ClientSession

    async with ClientSession() as session:
        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            msg_id = 1
            await ws.send_json({"id": msg_id, "method": "Page.enable"})
            msg_id = 2
            await ws.send_json(
                {"id": msg_id, "method": "Page.navigate", "params": {"url": url}}
            )
            deadline = asyncio.get_event_loop().time() + timeout
            got = False
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    if not got:
                        raise TimeoutError("CDP navigate timeout")
                    return
                raw = await asyncio.wait_for(ws.receive(), timeout=remaining)
                if raw.type.name == "TEXT":
                    data = json.loads(raw.data)
                    if data.get("id") == msg_id:
                        if "error" in data:
                            raise RuntimeError(str(data["error"]))
                        got = True
                        await asyncio.sleep(0.5)
                        return
                elif raw.type.name in ("CLOSED", "ERROR"):
                    raise RuntimeError("CDP websocket closed")


def cdp_navigate(ws_url: str, url: str) -> None:
    asyncio.run(_cdp_navigate(ws_url, url))


def wait_for_game_target(
    port: int,
    *,
    timeout_s: float = 55.0,
    progress: Callable[[str], None] | None = None,
) -> CdpTarget | None:
    """Poll CDP until a Pacific Front tab appears on ``port``."""
    deadline = time.time() + timeout_s
    last_note = 0.0
    while time.time() < deadline:
        best: CdpTarget | None = None
        best_score = -999
        for t in list_page_targets(port):
            s = _score_target(t)
            if s > best_score:
                best_score = s
                best = t
        if best is not None and best_score >= 10:
            return best
        now = time.time()
        if progress and now - last_note > 5.0:
            progress(f"等待游戏页加载…（剩余约 {int(deadline - now)}s）")
            last_note = now
        time.sleep(0.75)
    return None


async def _cdp_evaluate(ws_url: str, expression: str, timeout: float = 12.0) -> dict:
    from aiohttp import ClientSession

    async with ClientSession() as session:
        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            msg_id = 1
            await ws.send_json(
                {
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": False,
                        "returnByValue": True,
                        "userGesture": True,
                    },
                }
            )
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError("CDP evaluate timeout")
                raw = await asyncio.wait_for(ws.receive(), timeout=remaining)
                if raw.type.name == "TEXT":
                    data = json.loads(raw.data)
                    if data.get("id") == msg_id:
                        if "error" in data:
                            raise RuntimeError(str(data["error"]))
                        return data.get("result") or {}
                elif raw.type.name in ("CLOSED", "ERROR"):
                    raise RuntimeError("CDP websocket closed")


def inject_script_via_cdp(target: CdpTarget, script: str) -> None:
    """Synchronously inject JS into the target page via CDP."""
    wrapped = (
        "(function(){ try {\n"
        + script
        + "\n; return 'PF_INJECT_OK'; } catch(e) { return 'PF_INJECT_ERR:' + (e && e.message); } })()"
    )
    log.info("Injecting into [%s] %s (%s)", target.title[:40], target.url[:80], target.ws_url[:60])
    result = asyncio.run(_cdp_evaluate(target.ws_url, wrapped))
    val = None
    if isinstance(result, dict):
        rv = result.get("result") or {}
        val = rv.get("value")
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"]))
    log.info("Inject result: %s", val)
    if isinstance(val, str) and val.startswith("PF_INJECT_ERR:"):
        raise RuntimeError(val)


def _paste_hint() -> str:
    return (
        "IE 无自动注入：请点「复制注入脚本」，在游戏页 F12 控制台粘贴回车。"
    )


def one_click_connect(
    inject_script: str,
    *,
    allow_launch: bool = True,
    progress: Callable[[str], None] | None = None,
    already_connected: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """
    One-click connect:
    1) If bridge/inject already online → do nothing (never launch IE).
    2) If a CDP (Chrome/Edge debug) Pacific Front tab exists → inject.
    3) Else default: open Internet Explorer at game URL and ask user to paste inject
       (IE has no CDP). Chrome debug launch is NOT the default popup.
    Returns (ok, message).
    """
    def prog(m: str):
        log.info(m)
        if progress:
            progress(m)

    def connected() -> bool:
        try:
            return bool(already_connected and already_connected())
        except Exception:
            return False

    if connected():
        prog("页面已连接，跳过打开浏览器")
        return True, "页面已连接（无需打开浏览器）"

    def try_inject(t: CdpTarget) -> tuple[bool, str]:
        if connected():
            return True, "页面已连接（注入期间已握手）"
        prog(f"找到页面: {t.title or t.url}")
        try:
            inject_script_via_cdp(t, inject_script)
        except Exception as e:
            log.exception("inject failed")
            return False, f"自动注入失败: {e}\n请改用「复制注入脚本」手动粘贴。"
        return True, f"已注入 · {t.title or t.url}"

    prog("扫描调试浏览器…")
    target = find_best_target()
    if target is not None:
        return try_inject(target)

    ports = list_cdp_ports()

    # CDP up but no matched game tab — try navigate existing debug page
    if ports:
        pages: list[CdpTarget] = []
        for p in ports:
            pages.extend(list_page_targets(p))
        if allow_launch and pages:
            blankish = [
                t
                for t in pages
                if t.url.startswith(("about:", "chrome://", "edge://"))
                or not t.url.startswith("http")
            ]
            if blankish or all(_score_target(t) < 10 for t in pages):
                try:
                    prog("调试浏览器已开，正在导航到游戏首页…")
                    cdp_navigate(pages[0].ws_url, GAME_HOME_URL)
                except Exception as e:
                    log.warning("navigate existing CDP failed: %s", e)
                target = wait_for_game_target(ports[0], timeout_s=8.0, progress=prog)
                if target is not None:
                    return try_inject(target)
                return False, (
                    "已导航到游戏首页。请登录后，再点一次「一键连接」；"
                    "或用「复制注入脚本」手动粘贴。"
                )
        titles = ", ".join((t.title or t.url)[:30] for t in pages[:5]) or "(无标签)"
        return False, (
            f"已检测到调试浏览器（端口 {ports}），但未找到太平洋前线游戏标签。\n"
            f"当前页: {titles}\n"
            "请打开游戏页后再点「一键连接」，或用「复制注入脚本」。"
        )

    # No CDP → default popup is Internet Explorer + paste inject
    # Re-check bridge: user may have pasted inject while we scanned CDP.
    if connected():
        prog("页面已连接，取消打开 IE")
        return True, "页面已连接（无需打开浏览器）"

    if allow_launch and sys.platform == "win32":
        if connected():
            return True, "页面已连接（无需打开浏览器）"
        prog("未连接：正在用 IE 打开游戏页…")
        launched = launch_ie(GAME_HOME_URL)
        if launched:
            return False, (
                "已用 Internet Explorer 打开游戏页。\n"
                + _paste_hint()
                + "\n登录进入据点布阵页后粘贴即可连接。"
            )
        return False, (
            "未能启动 Internet Explorer。\n"
            + _paste_hint()
            + "\n也可在任意浏览器打开游戏后粘贴注入脚本。"
        )

    return False, (
        "未检测到已连接的游戏页。\n" + _paste_hint()
    )
