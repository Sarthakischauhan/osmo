"""Switching the voice: picker commits (`session.configure`) and the model's `set_voice`, validated against the
available models and applied to the live session. A voice or effort change updates the session in place; a model
is fixed per voice session, so a model switch opens a new one (seeded from the thread) and closes the old."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, Protocol

from core_ai.voice import VoiceConfig, VoiceSession

from symphony_server.model_access import ModelAccess
from symphony_server.protocol import OsmoState, ServerEvent, VoiceConfigured
from symphony_server.voices import Selection, SetVoiceArgs, resolve_selection

log = logging.getLogger("symphony.voice")

# A picker model switch waits this long for the current answer to finish.
SWAP_WAIT_S = 15.0

Source = Literal["client", "tool"]


class TurnState(Protocol):
    """What a switch needs of the turns (voice_turns.VoiceTurns): whether an answer is under way, and a deferred swap."""

    idle: asyncio.Event
    swap_after_turn: bool


class SwitchHost(Protocol):
    """What a switch needs from the session it switches (voice_bridge.VoiceBridge)."""

    selection: Selection
    voice: VoiceSession | None
    access: ModelAccess
    turns: TurnState

    async def emit(self, event: ServerEvent) -> None:
        """Send an event to the browser."""

    async def set_state(self, state: OsmoState, detail: str | None = None) -> None:
        """Tell the browser Osmo's state."""

    async def open_voice(self) -> bool:
        """Open a voice session on the selected model; False when it could not be."""

    def voice_config(self) -> VoiceConfig:
        """The voice session's settings for the current selection."""


class VoiceSwitch:
    """The one path for changing the voice, the model or the effort of a session."""

    def __init__(self, host: SwitchHost) -> None:
        self.host = host
        self.lock = asyncio.Lock()

    async def from_client(self, change: SetVoiceArgs) -> None:
        """A picker commit. A model switch waits for the current answer to finish (never cutting it off), then swaps
        and tells the browser the session is listening."""
        host = self.host
        async with self.lock:
            swapping = host.voice is not None and (change.model or host.selection.model) != host.selection.model
            if swapping:
                try:
                    async with asyncio.timeout(SWAP_WAIT_S):
                        await host.turns.idle.wait()
                except TimeoutError:
                    log.warning("model switch waited %.0fs for the answer to end; switching anyway", SWAP_WAIT_S)
            before = host.voice
            outcome = await self.configure(change, "client")
            if isinstance(outcome, Selection) and host.voice is not before:
                await host.set_state("listening")

    async def configure(self, change: SetVoiceArgs, source: Source) -> Selection | str:
        """Validate, apply, tell the browser. The browser always hears back with the applied selection, or the one
        still in effect plus `error`."""
        host = self.host
        picked = resolve_selection(host.access.models(), change, host.selection)
        if isinstance(picked, str):
            log.warning("rejected voice change from %s: %s", source, picked)
            await host.emit(VoiceConfigured(selection=host.selection, source=source, error=picked))
            return picked
        previous, host.selection = host.selection, picked
        if host.voice is None:
            await host.emit(VoiceConfigured(selection=picked, source=source))
            return picked
        if picked.model == previous.model:
            await host.voice.update(host.voice_config())
        elif source == "tool":
            # The answer that asked is still speaking its confirmation on this session.
            host.turns.swap_after_turn = True
            return picked
        elif not await self.swap():
            host.selection = previous
            error = f"could not open {picked.model}; still on {previous.model}"
            await host.emit(VoiceConfigured(selection=previous, source=source, error=error))
            return error
        await host.emit(VoiceConfigured(selection=picked, source=source))
        return picked

    async def apply_swap(self, source: Source) -> None:
        """Move onto the selected model now (a deferred `set_voice` switch), and tell the browser how it went."""
        error = None if await self.swap() else f"could not open {self.host.selection.model}"
        await self.host.emit(VoiceConfigured(selection=self.host.selection, source=source, error=error))

    async def swap(self) -> bool:
        """Open a voice session on the newly selected model, then close the old one. The browser socket stays."""
        old = self.host.voice
        if not await self.host.open_voice():
            return False
        if old is not None:
            await old.aclose()
        return True
