"""Gemini Live setup compatibility.

Gemini Live closes the WebSocket as soon as ``setup`` is rejected. The shared voice
config carries reasoning effort in the same lowercase words as the other providers
(``minimal``, ``low``, …). Live's ``thinkingConfig.thinkingLevel`` is the uppercase
enum (``MINIMAL``, ``LOW``, ``MEDIUM``, ``HIGH``); a lowercase value is an invalid
argument and the socket ends before ``setupComplete``.

core_ai builds that setup message, so this rewrites the first client frame on the
way out. ``gemini-3.8-live`` has no ``thinkingLevel`` at all and must omit it.
"""

from __future__ import annotations

import json
from types import MethodType
from typing import Any

from core_ai.voice.gemini.realtime import GeminiVoiceSession

# Gemini Live documents this endpoint as not supporting thinkingLevel:
# https://aistudio.google.com/docs/models/gemini-3.8-live
OMIT_THINKING_LEVEL = frozenset({"gemini-3.8-live"})

_LEVELS = {
    "none": "MINIMAL",
    "minimal": "MINIMAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
}


def thinking_level(effort: str) -> str:
    """The Live ``ThinkingLevel`` for a catalog effort, or the uppercased word."""
    return _LEVELS.get(effort.strip().lower(), effort.strip().upper())


def _normalize_payload(payload: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Return a copy with any unsupported or lowercase setup level corrected."""
    setup = payload.get("setup")
    if not isinstance(setup, dict):
        return payload
    generation = setup.get("generationConfig")
    if not isinstance(generation, dict):
        return payload
    thinking = generation.get("thinkingConfig")
    if not isinstance(thinking, dict) or "thinkingLevel" not in thinking:
        return payload
    thinking = thinking.copy()
    generation = generation.copy()
    if model_id.removeprefix("models/") in OMIT_THINKING_LEVEL:
        thinking.pop("thinkingLevel")
        if not thinking:
            generation.pop("thinkingConfig")
        else:
            generation["thinkingConfig"] = thinking
    else:
        level = thinking.get("thinkingLevel")
        if not isinstance(level, str):
            return payload
        thinking["thinkingLevel"] = thinking_level(level)
        generation["thinkingConfig"] = thinking
    return {**payload, "setup": {**setup, "generationConfig": generation}}


def normalize_setup(frame: str, model_id: str) -> str:
    """Uppercase ``thinkingLevel`` in a setup frame, or drop it when the model rejects it."""
    try:
        payload = json.loads(frame)
    except (ValueError, TypeError):
        return frame
    if not isinstance(payload, dict):
        return frame
    fixed = _normalize_payload(payload, model_id)
    return frame if fixed is payload else json.dumps(fixed)


def install_gemini_live(session: GeminiVoiceSession) -> None:
    """Rewrite setup frames so Live accepts the session instead of closing it."""
    original = session._send

    async def send(_session: Any, payload: dict[str, Any]) -> None:
        await original(_normalize_payload(payload, session.model_id))

    session._send = MethodType(send, session)  # type: ignore[method-assign]
