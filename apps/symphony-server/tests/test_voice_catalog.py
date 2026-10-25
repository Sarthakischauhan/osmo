"""xAI's voice roster: the live `GET /v1/tts/voices` list, its cache, the documented fallback, and a
preview for every voice against a fake realtime upstream."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from conftest import make_client
from symphony_server.file_store import FileSecretStore
from symphony_server.providers import CredentialStore
from symphony_server.settings import Settings
from symphony_server.voices import (
    DOCUMENTED_VOICES,
    PREVIEW_BYTES,
    XAI_VOICES_URL,
    PreviewCache,
    VoiceCatalog,
)

KEY = "xai-test-key-secret"
LISTING = {
    "voices": [
        {"voice_id": "eve", "name": "Eve", "language": "en"},
        {"voice_id": "Rex", "name": "Rex", "language": "en"},
        {"voice_id": "nova-new", "name": "Nova New", "language": None},
    ]
}


class VoiceList:
    def __init__(self, status: int = 200, body: object = LISTING) -> None:
        self.status, self.body, self.calls = status, body, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == XAI_VOICES_URL and request.headers["Authorization"] == f"Bearer {KEY}"
        self.calls += 1
        return httpx.Response(self.status, json=self.body)


def settings(tmp_path: Path) -> Settings:
    return Settings(xai_api_key=KEY, realtime_url="ws://127.0.0.1:9/unused", credentials_file=tmp_path / "creds.enc")


def voice_ids(client_response: httpx.Response) -> list[list[str]]:
    return [[v["id"] for v in m["voices"]] for m in client_response.json()["models"]]


def test_documented_roster_has_every_voice_in_xais_voice_table() -> None:
    ids = [v.id for v in DOCUMENTED_VOICES]
    assert len(ids) == len(set(ids)) == 28
    assert {"eve", "ara", "sal", "leo", "rex", "carina", "naksh", "aurora", "liora"} <= set(ids)
    assert all(v.traits for v in DOCUMENTED_VOICES)


def test_live_voice_list_replaces_the_roster_and_is_cached(tmp_path: Path) -> None:
    upstream = VoiceList()
    with make_client(settings(tmp_path), voices=upstream) as client:
        first = client.get("/voice-models")
        second = client.get("/voice-models")
    assert voice_ids(first) == voice_ids(second) == [["eve", "rex", "nova-new"]] * 2
    voices = {v["id"]: v for v in first.json()["models"][0]["voices"]}
    assert voices["nova-new"] == {"id": "nova-new", "label": "Nova New", "traits": []}
    assert voices["rex"]["traits"] == ["confident", "clear"]
    assert upstream.calls == 1
    assert KEY not in first.text


@pytest.mark.parametrize("upstream", [VoiceList(status=500), VoiceList(body={"unexpected": True})])
def test_failed_voice_list_falls_back_to_the_documented_roster(tmp_path: Path, upstream: VoiceList) -> None:
    with make_client(settings(tmp_path), voices=upstream) as client:
        ids = voice_ids(client.get("/voice-models"))
    assert ids == [[v.id for v in DOCUMENTED_VOICES]] * 2


def test_no_xai_key_means_no_fetch(tmp_path: Path) -> None:
    upstream = VoiceList()
    with make_client(Settings(xai_api_key=None, realtime_url="ws://127.0.0.1:9/unused", credentials_file=tmp_path / "c.enc"), voices=upstream) as client:
        assert voice_ids(client.get("/voice-models"))[0] == [v.id for v in DOCUMENTED_VOICES]
    assert upstream.calls == 0


async def test_catalog_refetches_after_its_ttl() -> None:
    now = [0.0]
    upstream = VoiceList()
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    catalog = VoiceCatalog(client, CredentialStore(FileSecretStore(None), env={"XAI_API_KEY": KEY}), clock=lambda: now[0])
    await catalog.refresh()
    now[0] = 3599.0
    await catalog.refresh()
    assert upstream.calls == 1
    now[0] = 3601.0
    await catalog.refresh()
    assert upstream.calls == 2
    await client.aclose()


@pytest.mark.parametrize("voice", [v.id for v in DOCUMENTED_VOICES])
async def test_every_documented_voice_previews(voice: str) -> None:
    """A fake realtime upstream answers only when session.update carries the requested voice."""
    asked: list[str] = []

    async def handler(conn: ServerConnection) -> None:
        update = json.loads(await conn.recv())
        asked.append(update["session"]["voice"])
        if update["session"]["voice"] != voice:
            await conn.close()
            return
        chunk = base64.b64encode(b"\x01\x00" * (PREVIEW_BYTES // 2)).decode()
        await conn.send(json.dumps({"type": "response.output_audio.delta", "delta": chunk}))
        await conn.send(json.dumps({"type": "response.done"}))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        cache = PreviewCache(f"ws://127.0.0.1:{port}/v1/realtime", CredentialStore(FileSecretStore(None), env={"XAI_API_KEY": KEY}), None)
        audio = await cache.get("grok-voice-latest", voice)
    assert asked == [voice]
    assert len(audio) == PREVIEW_BYTES


def test_locked_registry_still_has_a_complete_default_selection(tmp_path: Path) -> None:
    """With xAI not connected the picker must still parse `/voice-models` (to offer Connect)."""
    plain = Settings(xai_api_key=None, realtime_url="ws://127.0.0.1:9/unused", credentials_file=tmp_path / "c.enc")
    with make_client(plain) as client:
        body = client.get("/voice-models").json()
    assert all(m["locked"] for m in body["models"])
    assert body["selection"] == {"model": "grok-voice-latest", "voice": "eve", "effort": "high"}
