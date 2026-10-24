"""ASGI request-body size enforcement for run creation."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict


class _RequestTooLarge(Exception):
    pass


class RequestSizeLimitMiddleware:
    """Reject oversized run bodies while they are being received."""

    def __init__(self, app: Any, *, max_bytes: int, path: str = "/runs") -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path = path

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Dict[str, Any]]],
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.path
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await self._reject(send)
                        return
                except ValueError:
                    pass

        received = 0
        response_started = False

        async def limited_receive() -> Dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _RequestTooLarge
            return message

        async def tracked_send(message: Dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestTooLarge:
            if response_started:
                raise
            await self._reject(send)

    async def _reject(
        self,
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        body = json.dumps({"detail": "Request body is too large"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
