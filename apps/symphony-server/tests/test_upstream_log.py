"""OSMO_DEBUG_UPSTREAM capture: off by default, JSONL when on, audio as lengths, no secrets."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeGrok, make_client
from symphony_server.settings import Settings
from test_session import say, start, wait_listening

API_KEY = "xai-super-secret-key"


def run_session(grok: FakeGrok, settings: Settings) -> None:
    with make_client(settings) as client, client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "hello")
        grok.send({"type": "response.created", "response": {"id": "r1"}})
        grok.send({"type": "response.output_audio.delta", "response_id": "r1", "delta": base64.b64encode(b"\x00" * 48).decode()})
        grok.send(b"\x01\x00" * 10)
        grok.send({"type": "session.updated", "session": {"client_secret": {"value": "tok-123"}, "api_key": "k"}})
        grok.send({"type": "response.done", "response": {"id": "r1"}})
        wait_listening(ws)


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_off_by_default_writes_nothing(grok: FakeGrok, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OSMO_DEBUG_UPSTREAM", raising=False)
    monkeypatch.setenv("XAI_API_KEY", API_KEY)
    monkeypatch.setenv("XAI_BASE_URL", grok.base_url)
    settings = Settings.from_env()
    assert settings.debug_upstream_file is None
    run_session(grok, settings)
    assert list(tmp_path.iterdir()) == []


def test_on_writes_redacted_jsonl(grok: FakeGrok, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "capture.jsonl"
    monkeypatch.setenv("OSMO_DEBUG_UPSTREAM", "1")
    monkeypatch.setenv("OSMO_DEBUG_UPSTREAM_FILE", str(target))
    monkeypatch.setenv("XAI_API_KEY", API_KEY)
    monkeypatch.setenv("XAI_BASE_URL", grok.base_url)
    run_session(grok, Settings.from_env())

    lines = read_lines(target)
    assert all({"ts", "direction", "type", "payload"} <= set(line) for line in lines)
    assert ("sent", "session.update") == (lines[0]["direction"], lines[0]["type"])
    assert "tools" in lines[0]["payload"]["session"]
    by_type = {line["type"]: line for line in lines if line["direction"] == "received"}
    assert {"response.created", "response.output_audio.delta", "binary_audio", "session.updated", "response.done"} <= set(by_type)
    assert by_type["response.output_audio.delta"]["payload"]["delta"] == {"audio_bytes": 48}
    assert by_type["binary_audio"]["payload"] == {"audio_bytes": 20}
    assert by_type["session.updated"]["payload"]["session"] == {"client_secret": "[redacted]", "api_key": "[redacted]"}

    text = target.read_text()
    for secret in (API_KEY, "Bearer", "tok-123", "Authorization"):
        assert secret not in text
