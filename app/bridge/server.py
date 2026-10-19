"""Localhost HTTP + WebSocket bridge for the game-page inject script."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from aiohttp import WSMsgType, web
from PySide6.QtCore import QObject, Signal

from ..paths import resource_path

log = logging.getLogger("pf.bridge")


class BridgeSignals(QObject):
    client_connected = Signal(int)  # client count
    client_disconnected = Signal(int)
    snapshot_received = Signal(dict)
    deploy_result = Signal(dict)
    deploy_plan = Signal(dict)
    auto_result = Signal(dict)
    status = Signal(str)
    error = Signal(str)


class BridgeServer:
    """Runs aiohttp on a background thread; emits Qt signals on the main thread."""

    def __init__(self, port: int = 17891):
        self.port = port
        self.signals = BridgeSignals()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: web.AppRunner | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._start_error: str | None = None

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, name="pf-bridge", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=8):
            raise RuntimeError("桥接服务启动超时")
        if self._start_error:
            raise RuntimeError(self._start_error)

    def stop(self) -> None:
        if not self._loop:
            return
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._loop = None

    def request_scrape(self) -> bool:
        return self._broadcast({"type": "scrape"})

    def request_deploy(self, slots: list[dict | None], *, clear: bool = True) -> bool:
        """Ask page to place ships via Redux battleBoard (type=deploy).

        Does not start a battle. Automation uses request_start_battle separately.
        """
        payload = {
            "type": "deploy",
            "slots": slots,
            "clear": clear,
        }
        return self._broadcast(payload)

    def request_open_keypoint(self, operation_id: str, keypoint_id: str) -> bool:
        return self._broadcast({
            "type": "open_keypoint",
            "operationId": operation_id,
            "keypointId": keypoint_id,
        })

    def request_start_battle(self) -> bool:
        """Automation only: ask the page to click 「战斗」. Not used by manual deploy."""
        return self._broadcast({"type": "start_battle"})

    def request_fetch_operation(self, operation_id: str) -> bool:
        """Read-only GET of one operation. Does not spend currency or change settings."""
        return self._broadcast({
            "type": "fetch_operation",
            "operationId": operation_id,
        })

    def inject_script_text(self) -> str:
        path = resource_path("bridge", "inject.js")
        if not path.is_file():
            # Fallback relative to package
            alt = Path(__file__).resolve().parent.parent.parent / "bridge" / "inject.js"
            path = alt
        text = path.read_text(encoding="utf-8")
        # Bake default port hint
        return text.replace("__PF_BRIDGE_PORT__", str(self.port))

    def bookmarklet(self) -> str:
        # Minimal loader that fetches inject from local HTTP
        return (
            f"javascript:(function(){{var s=document.createElement('script');"
            f"s.src='http://127.0.0.1:{self.port}/inject.js?port={self.port}&t='+Date.now();"
            f"document.documentElement.appendChild(s);}})();"
        )

    def _broadcast(self, msg: dict) -> bool:
        if not self._loop:
            return False
        with self._lock:
            clients = list(self._clients)
        if not clients:
            return False
        data = json.dumps(msg, ensure_ascii=False)

        async def _send_all():
            dead = []
            for ws in clients:
                try:
                    await ws.send_str(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                with self._lock:
                    self._clients.discard(ws)

        asyncio.run_coroutine_threadsafe(_send_all(), self._loop)
        return True

    def _run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(self._setup())
            self._started.set()
            loop.run_forever()
        except Exception as e:
            self._start_error = str(e)
            self._started.set()
            self.signals.error.emit(str(e))
            log.exception("bridge failed")
        finally:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.stop()
            except Exception:
                pass

    async def _setup(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/inject.js", self._handle_inject)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_post("/api/snapshot", self._handle_snapshot_http)
        app.router.add_options("/api/snapshot", self._handle_cors_preflight)
        app.router.add_get("/api/status", self._handle_status)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            site = web.TCPSite(self._runner, "127.0.0.1", self.port)
            await site.start()
        except OSError as e:
            raise RuntimeError(f"端口 {self.port} 无法绑定（可能已被占用）: {e}") from e
        self.signals.status.emit(f"桥接已监听 127.0.0.1:{self.port}")

    async def _shutdown(self) -> None:
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _cors_headers(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

    async def _handle_cors_preflight(self, request: web.Request) -> web.Response:
        return web.Response(status=204, headers=self._cors_headers())

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = (
            "<html><body style='background:#1c1c1f;color:#ddd;font-family:sans-serif'>"
            f"<h3>窝牌计算器 Ver 1.0.0 桥接</h3>"
            f"<p>端口 {self.port} · WS 客户端 {self.client_count}</p>"
            f"<p>注入: <a href='/inject.js'>/inject.js</a></p>"
            "</body></html>"
        )
        return web.Response(text=html, content_type="text/html", headers=self._cors_headers())

    async def _handle_inject(self, request: web.Request) -> web.Response:
        try:
            text = self.inject_script_text()
        except Exception as e:
            return web.Response(text=f"/* error: {e} */", status=500, content_type="application/javascript")
        headers = {
            **self._cors_headers(),
            "Cache-Control": "no-store",
            "Content-Type": "application/javascript; charset=utf-8",
        }
        return web.Response(text=text, headers=headers)

    async def _handle_status(self, request: web.Request) -> web.Response:
        body = {"ok": True, "port": self.port, "clients": self.client_count}
        return web.json_response(body, headers=self._cors_headers())

    async def _handle_snapshot_http(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid json"},
                status=400,
                headers=self._cors_headers(),
            )
        if data.get("type") != "snapshot":
            data = {"type": "snapshot", **data}
        self.signals.snapshot_received.emit(data)
        return web.json_response({"ok": True}, headers=self._cors_headers())

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024)
        await ws.prepare(request)
        with self._lock:
            self._clients.add(ws)
            n = len(self._clients)
        self.signals.client_connected.emit(n)
        self.signals.status.emit(f"页面已连接 ({n})")
        try:
            # Ask for scrape on connect
            await ws.send_str(json.dumps({"type": "hello", "port": self.port}))
            await ws.send_str(json.dumps({"type": "scrape"}))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    self._on_message(data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                    break
        finally:
            with self._lock:
                self._clients.discard(ws)
                n = len(self._clients)
            self.signals.client_disconnected.emit(n)
            self.signals.status.emit(f"页面断开 ({n})" if n else "未连接页面")
        return ws

    def _on_message(self, data: dict) -> None:
        typ = data.get("type")
        if typ == "snapshot":
            self.signals.snapshot_received.emit(data)
        elif typ == "deploy_plan":
            self.signals.deploy_plan.emit(data)
        elif typ == "deploy_result":
            self.signals.deploy_result.emit(data)
        elif typ == "auto_result":
            self.signals.auto_result.emit(data)
        elif typ == "log":
            self.signals.status.emit(str(data.get("message") or data))
        elif typ == "error":
            self.signals.error.emit(str(data.get("message") or data))
