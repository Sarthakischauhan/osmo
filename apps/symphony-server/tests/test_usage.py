"""Model usage: stored per thread, priced only from the catalog, served at /api/usage and sent live."""

from __future__ import annotations

import json
import time
from pathlib import Path

from conftest import FakeGrok, make_client, xai_key_store
from core_ai.voice import ResponseDone
from core_harness import ControlPlaneEvent
from test_agent_phases import runs_with_observer
from test_session import next_event, say

from symphony_server.protocol import UsageEvent
from symphony_server.settings import Settings
from symphony_server.storage import SQLiteStorage, UsageRecord
from symphony_server.usage import AgentUsagePayload, agent_usage, voice_usage


def row(thread: str, at: float, model: str = "grok:grok-4.7") -> UsageRecord:
    return UsageRecord(threadId=thread, source="agent", model=model, inputTokens=10, outputTokens=5, costUsd=0.001, createdAt=at)


def test_usage_is_stored_newest_first_by_time_and_thread() -> None:
    storage = SQLiteStorage(None)
    for thread, at in (("t1", 100.0), ("t2", 200.0), ("t1", 300.0), ("t1", 50.0)):
        storage.add_usage(row(thread, at))
    assert [u.createdAt for u in storage.usage_since(60)] == [300.0, 200.0, 100.0]
    assert [u.createdAt for u in storage.usage_since(60, "t1")] == [300.0, 100.0]
    assert [u.createdAt for u in storage.usage_since(0, limit=2)] == [300.0, 200.0]
    stored = storage.usage_since(0)[0]
    assert stored == row("t1", 300.0)


def test_usage_outlives_its_thread() -> None:
    storage = SQLiteStorage(None)
    thread = storage.create_thread()
    storage.add_usage(row(thread.id, 1.0))
    storage.delete_thread(thread.id)
    assert len(storage.usage_since(0)) == 1


def test_an_agent_turn_keeps_the_harness_cost_only_for_a_catalog_model() -> None:
    priced = agent_usage("t1", "r1", AgentUsagePayload(prompt_tokens=1000, completion_tokens=200, model_id="grok:grok-4.7", session_cost=0.0032))
    assert (priced.costUsd, priced.cachedTokens, priced.source, priced.runId) == (0.0032, None, "agent", "r1")
    unknown = agent_usage("t1", "r1", AgentUsagePayload(prompt_tokens=1000, completion_tokens=200, model_id="local:mystery", session_cost=0.0))
    assert unknown.costUsd is None
    estimated = agent_usage("t1", "r1", AgentUsagePayload(prompt_tokens=1, model_id="grok:grok-4.7", estimated=True))
    assert estimated.estimated


def test_a_voice_response_is_counted_but_never_priced() -> None:
    usage = voice_usage("t1", "xai:grok-voice-latest", ResponseDone(input_tokens=120, output_tokens=40))
    assert usage is not None and (usage.inputTokens, usage.outputTokens, usage.costUsd, usage.source) == (120, 40, None, "voice")
    assert voice_usage("t1", "xai:grok-voice-latest", ResponseDone()) is None


async def test_an_agent_usage_event_is_stored_and_shown_on_its_thread() -> None:
    runs, observer = runs_with_observer()
    payload = {"turn": 1, "prompt_tokens": 900, "completion_tokens": 100, "reasoning_tokens": 0, "total_tokens": 1000, "model_id": "grok:grok-4.7", "session_cost": 0.0024}
    await runs.on_event("r1", ControlPlaneEvent(event_type="usage", payload=payload, run_id="r1", session_id="s1", sequence=1))
    [stored] = runs.storage.usage_since(0)
    assert (stored.threadId, stored.runId, stored.inputTokens, stored.costUsd) == ("t1", "r1", 900, 0.0024)
    assert observer.shown == [UsageEvent(usage=stored)]


def test_the_usage_endpoint_filters_by_range_and_thread(tmp_path: Path) -> None:
    now = time.time()
    storage = SQLiteStorage(tmp_path / "osmo.db")
    storage.add_usage(row("t1", now - 60))
    storage.add_usage(row("t2", now - 120))
    storage.add_usage(row("t1", now - 3 * 86_400))
    storage.close()
    with make_client(Settings(database_file=tmp_path / "osmo.db")) as client:
        assert len(client.get("/api/usage").json()["usage"]) == 3
        assert len(client.get("/api/usage?days=1").json()["usage"]) == 2
        assert [u["threadId"] for u in client.get("/api/usage?days=1&thread=t1").json()["usage"]] == ["t1"]
        assert client.get("/api/usage?days=91").status_code == 422


def test_a_voice_response_with_tokens_ticks_the_usage_dialog(grok: FakeGrok) -> None:
    with make_client(Settings(xai_base_url=grok.base_url), secret_store=xai_key_store()) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "session.start", "voice": "eve"}))
            thread = next_event(ws, "thread")["thread"]["id"]
            assert next_event(ws, "state")["state"] == "listening"
            say(grok, "hi")
            grok.send({"type": "response.created", "response": {"id": "r1"}})
            grok.send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "Hello."})
            grok.send({"type": "response.done", "response": {"id": "r1", "usage": {"input_tokens": 321, "output_tokens": 54}}})
            usage = next_event(ws, "usage")["usage"]
            assert (usage["threadId"], usage["source"], usage["inputTokens"], usage["outputTokens"], usage.get("costUsd")) == (thread, "voice", 321, 54, None)
        assert client.get(f"/api/usage?thread={thread}").json()["usage"][0]["inputTokens"] == 321
