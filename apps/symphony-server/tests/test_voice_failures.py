"""A missing provider transcript must produce an actionable client event."""

from __future__ import annotations

import pytest

from symphony_server.protocol import TranscriptDelta, VoiceErrorEvent
from symphony_server.voice_turns import VoiceTurns
import symphony_server.voice_turns as turns_module


class Host:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.states: list[tuple[str, str | None]] = []

    async def emit(self, event: object) -> None:
        self.events.append(event)

    async def set_state(self, state: str, detail: str | None = None) -> None:
        self.states.append((state, detail))


class MissingTranscript:
    def forget(self, utterance: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_transcript_timeout_is_visible_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(turns_module, "UNHEARD_WAIT_S", 0.0)
    host = Host()
    turns = VoiceTurns(host, None, MissingTranscript(), None)  # type: ignore[arg-type]
    await turns.expire("u1")
    assert isinstance(host.events[0], TranscriptDelta)
    assert isinstance(host.events[1], VoiceErrorEvent)
    assert host.events[1].code == "transcript_timeout"
    assert host.states[-1][0] == "listening"
