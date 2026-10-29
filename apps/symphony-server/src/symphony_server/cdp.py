"""A minimal Chrome DevTools Protocol client: JSON commands and events over one WebSocket.

Commands are matched to replies by id; events go to the handlers registered for their method.
Flattened sessions (`Target.attachToTarget` with `flatten`) share the socket, so each command
and event carries the `sessionId` of the page it belongs to.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("symphony.cdp")

EventHandler = Callable[[dict[str, Any], str | None], None]


class BrowserError(RuntimeError):
    """The browser could not do what a tool asked; the message is for the agent."""


class CdpError(BrowserError):
    """A CDP command failed, or the browser connection is gone."""


class CdpReply(BaseModel):
    """One message from the browser: a reply to a command (`id`) or an event (`method`)."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    method: str | None = None
    params: dict[str, Any] = {}
    result: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    sessionId: str | None = None


class CdpConnection:
    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket
        self.next_id = 0
        self.replies: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.handlers: defaultdict[str, list[EventHandler]] = defaultdict(list)
        # One-shot waiters for the next event of a method on a session.
        self.waiters: list[tuple[str, str | None, asyncio.Future[dict[str, Any]]]] = []
        self.reader = asyncio.create_task(self.read())

    @classmethod
    async def open(cls, url: str) -> CdpConnection:
        # A screencast frame is a whole page image: websockets' default 1 MiB message cap would cut it off.
        return cls(await connect(url, max_size=None))

    def on(self, method: str, handler: EventHandler) -> None:
        self.handlers[method].append(handler)

    def expect(self, method: str, session_id: str | None) -> asyncio.Future[dict[str, Any]]:
        """The params of the next `method` event on `session_id`; failed if the connection ends first."""
        waiter: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        entry = (method, session_id, waiter)
        self.waiters.append(entry)
        waiter.add_done_callback(lambda _: self.waiters.remove(entry))
        return waiter

    async def call(self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None) -> dict[str, Any]:
        self.next_id += 1
        command_id = self.next_id
        message: dict[str, Any] = {"id": command_id, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        reply: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.replies[command_id] = reply
        try:
            try:
                await self.socket.send(json.dumps(message))
            except ConnectionClosed as exc:
                raise CdpError("the browser connection is closed") from exc
            return await reply
        finally:
            self.replies.pop(command_id, None)

    async def read(self) -> None:
        try:
            async for raw in self.socket:
                self.dispatch(CdpReply.model_validate_json(raw))
        except ConnectionClosed:
            pass
        finally:
            lost = CdpError("the browser connection is closed")
            for pending in (*self.replies.values(), *(waiter for _, _, waiter in self.waiters)):
                if not pending.done():
                    pending.set_exception(lost)

    def dispatch(self, message: CdpReply) -> None:
        if message.id is not None:
            reply = self.replies.get(message.id)
            if reply is None or reply.done():
                return
            if message.error is not None:
                reply.set_exception(CdpError(str(message.error.get("message", message.error))))
            else:
                reply.set_result(message.result)
            return
        if message.method is None:
            return
        for method, session_id, waiter in tuple(self.waiters):
            if method == message.method and session_id == message.sessionId and not waiter.done():
                waiter.set_result(message.params)
        for handler in self.handlers[message.method]:
            handler(message.params, message.sessionId)

    async def close(self) -> None:
        self.reader.cancel()
        await self.socket.close()
