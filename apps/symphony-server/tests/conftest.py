"""Hermetic fixtures: a fake Grok realtime server and an app wired to it."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from websockets.asyncio.server import ServerConnection, serve

from symphony_server.app import create_app
from core_ai.voice import VoiceRegistry
from symphony_server.file_store import FileSecretStore
from symphony_server.secret_store import DEFAULT_OWNER, ProviderAuth, SecretStore
from symphony_server.settings import Settings

# Hermetic per-model context windows (xAI documents none; the server requires them configured).


class FakeGrok:
    """A local realtime endpoint. Records what Symphony sends; replays what tests push."""

    def __init__(self) -> None:
        self.received: queue.Queue[str | bytes] = queue.Queue()
        self.headers: queue.Queue[str] = queue.Queue()
        # Request path (with `?model=`) of each upstream connection, and the path of each one that closed.
        self.paths: queue.Queue[str] = queue.Queue()
        self.closed: queue.Queue[str] = queue.Queue()
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
            path = conn.request.path if conn.request else ""
            self.paths.put(path)
            outbox = self.outbox
            assert outbox is not None

            requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            async def push() -> None:
                while True:
                    await conn.send(await echo_metadata(await outbox.get(), requests))

            pusher = asyncio.create_task(push())
            try:
                async for message in conn:
                    self.received.put(message)
                    record_request(message, requests)
            finally:
                pusher.cancel()
                self.closed.put(path)

        async def main() -> None:
            server = await serve(handler, "127.0.0.1", 0)
            self.port = next(iter(server.sockets)).getsockname()[1]
            ready.set()
            await server.serve_forever()

        self.loop.run_until_complete(main())

    @property
    def base_url(self) -> str:
        """xAI's REST base on this server; core_ai opens `ws://…/v1/realtime?model=…` from it."""
        return f"http://127.0.0.1:{self.port}/v1"

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


# How long the fake waits for a pending `response.create` before treating a pushed
# `response.created` as a server-VAD response (null metadata).
REQUEST_WAIT_S = 0.2


def record_request(message: str | bytes, requests: asyncio.Queue[dict[str, Any]]) -> None:
    """Queue the metadata of each `response.create` the client sends, as xAI does."""
    if isinstance(message, str):
        event = json.loads(message)
        if event["type"] == "response.create":
            requests.put_nowait(event.get("response", {}).get("metadata"))


async def echo_metadata(payload: str | bytes, requests: asyncio.Queue[dict[str, Any]]) -> str | bytes:
    """Echo the oldest pending request's metadata on a test-pushed `response.created`
    (docs.x.ai: response.create metadata is returned on response.created/done; server-VAD
    responses carry null metadata)."""
    if isinstance(payload, bytes):
        return payload
    event = json.loads(payload)
    if event["type"] != "response.created" or "metadata" in event["response"]:
        return payload
    try:
        event["response"]["metadata"] = await asyncio.wait_for(requests.get(), REQUEST_WAIT_S)
    except TimeoutError:
        event["response"]["metadata"] = None
    return json.dumps(event)


@pytest.fixture
def grok() -> FakeGrok:
    return FakeGrok()


def unreachable(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected network call to {request.url}")


def make_client(
    settings: Settings,
    handler: Callable[[httpx.Request], httpx.Response] = unreachable,
    secret_store: SecretStore | None = None,
    voices: VoiceRegistry | None = None,
) -> TestClient:
    """The app with every outbound HTTP call answered by `handler` (by default: fail the test)."""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TestClient(create_app(settings, http_client=http_client, secret_store=secret_store, voices=voices))


def xai_key_store(key: str = "test-key") -> SecretStore:
    """An in-memory SecretStore holding an xAI key, as the provider auth flow would leave it."""
    store = FileSecretStore(None)
    store.put(DEFAULT_OWNER, ProviderAuth(provider="xai", kind="api_key", secret=SecretStr(key)))
    return store


@pytest.fixture
def client(grok: FakeGrok) -> Iterator[TestClient]:
    with make_client(Settings(xai_base_url=grok.base_url), secret_store=xai_key_store()) as test_client:
        yield test_client
