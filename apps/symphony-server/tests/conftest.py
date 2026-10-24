"""Hermetic fixtures: a fake Grok realtime server and an app wired to it."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from core_ai.registry import ModelRegistry
from core_server import build_config
from fastapi.testclient import TestClient
from websockets.asyncio.server import ServerConnection, serve

from symphony_server.app import create_app, orb_tools
from symphony_server.settings import Settings


class FakeGrok:
    """A local realtime endpoint. Records what Symphony sends; replays what tests push."""

    def __init__(self) -> None:
        self.received: queue.Queue[str | bytes] = queue.Queue()
        self.headers: queue.Queue[str] = queue.Queue()
        self.loop = asyncio.new_event_loop()
        self.outbox: asyncio.Queue[str | bytes] | None = None
        self.port = 0
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self.thread.start()
        ready.wait(5)

    def _run(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self.loop)
        self.outbox = asyncio.Queue()

        async def handler(conn: ServerConnection) -> None:
            self.headers.put(conn.request.headers.get("Authorization", "") if conn.request else "")
            outbox = self.outbox
            assert outbox is not None

            async def push() -> None:
                while True:
                    await conn.send(await outbox.get())

            pusher = asyncio.create_task(push())
            try:
                async for message in conn:
                    self.received.put(message)
            finally:
                pusher.cancel()

        async def main() -> None:
            server = await serve(handler, "127.0.0.1", 0)
            self.port = next(iter(server.sockets)).getsockname()[1]
            ready.set()
            await server.serve_forever()

        self.loop.run_until_complete(main())

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v1/realtime?model=grok-voice-latest"

    def send(self, message: dict[str, Any] | bytes) -> None:
        payload: str | bytes = message if isinstance(message, bytes) else json.dumps(message)
        outbox = self.outbox
        assert outbox is not None
        self.loop.call_soon_threadsafe(outbox.put_nowait, payload)

    def next_json(self, timeout: float = 5) -> dict[str, Any]:
        while True:
            message = self.received.get(timeout=timeout)
            if isinstance(message, str):
                parsed: dict[str, Any] = json.loads(message)
                return parsed

    def next_of(self, event_type: str, timeout: float = 5) -> dict[str, Any]:
        while True:
            event = self.next_json(timeout)
            if event["type"] == event_type:
                return event

    def next_bytes(self, timeout: float = 5) -> bytes:
        while True:
            message = self.received.get(timeout=timeout)
            if isinstance(message, bytes):
                return message


@pytest.fixture
def grok() -> FakeGrok:
    return FakeGrok()


def unreachable(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected network call to {request.url}")


def make_client(settings: Settings) -> TestClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
    tools = orb_tools(http_client)
    config = build_config(registry=ModelRegistry(), model_id="grok:grok-test", tools=list(tools.values()))
    return TestClient(create_app(settings, core_config=config, tools=tools, http_client=http_client))


@pytest.fixture
def client(grok: FakeGrok) -> Iterator[TestClient]:
    with make_client(Settings(xai_api_key="test-key", realtime_url=grok.url)) as test_client:
        yield test_client
