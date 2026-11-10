"""
Async Chrome DevTools Protocol (CDP) WebSocket Client.

Provides direct, high-performance communication with Headless Chromium (Microsoft Edge)
over WebSockets without third-party Node.js / Puppeteer dependencies.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional
import websockets

logger = logging.getLogger(__name__)


class CDPClient:
    """Lightweight asynchronous CDP client managing a single browser/page connection."""

    def __init__(self, ws_url: str, max_payload_bytes: int = 64 * 1024 * 1024):
        self.ws_url = ws_url
        self.max_payload_bytes = max_payload_bytes
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._msg_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._receiver_task: Optional[asyncio.Task] = None
        self._is_closed = False

    async def connect(self) -> None:
        """Establish the WebSocket connection to the DevTools endpoint."""
        self.ws = await websockets.connect(
            self.ws_url,
            max_size=self.max_payload_bytes,
            ping_interval=None,
        )
        self._receiver_task = asyncio.create_task(self._listen_loop())
        logger.debug("Connected to CDP at %s", self.ws_url)

    async def _listen_loop(self) -> None:
        """Background loop receiving and dispatching incoming JSON-RPC messages."""
        try:
            while not self._is_closed and self.ws:
                raw_msg = await self.ws.recv()
                msg = json.loads(raw_msg)
                req_id = msg.get("id")
                if req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(f"CDP Error: {msg['error']}"))
                        else:
                            fut.set_result(msg.get("result", {}))
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.warning("CDP receiver loop exited: %s", e)

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
        """Send a JSON-RPC method call and wait for the correlated response."""
        if self._is_closed or not self.ws:
            raise RuntimeError("CDP client is not connected.")

        self._msg_id += 1
        req_id = self._msg_id
        payload = {"id": req_id, "method": method, "params": params or {}}

        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        await self.ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise TimeoutError(f"CDP call {method} timed out after {timeout}s")

    async def evaluate(self, expression: str, await_promise: bool = False, return_by_value: bool = True, timeout: float = 30.0) -> Any:
        """Evaluate a JavaScript expression in the page context and return the unwrapped value."""
        params = {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": return_by_value,
        }
        res = await self.send("Runtime.evaluate", params, timeout=timeout)
        if "exceptionDetails" in res:
            exc = res["exceptionDetails"]
            err_msg = exc.get("exception", {}).get("description", exc.get("text", "Unknown JS error"))
            raise RuntimeError(f"JavaScript evaluation error: {err_msg}")
        result_obj = res.get("result", {})
        if "value" in result_obj:
            return result_obj["value"]
        if result_obj.get("type") == "undefined":
            return None
        return result_obj

    async def close(self) -> None:
        """Gracefully close the WebSocket and cancel the listener task."""
        self._is_closed = True
        if self._receiver_task:
            self._receiver_task.cancel()
        if self.ws:
            await self.ws.close()
            self.ws = None
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()
