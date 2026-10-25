"""The `grok` provider every harness run streams from: voice models answer on the run's open
voice link, every other model (and image generation) on core_ai's GrokProvider.

The harness has no per-run model-call hook, so the run's link travels in a ContextVar that
VoiceRunLink (a core_server addon_factories entry, built per run) sets in `before_run`; each run
executes in its own task, so runs never see each other's link."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextvars import ContextVar
from typing import Any

from core_ai import GrokProvider
from core_ai.providers.base import BaseProvider
from core_ai.types import Message, StreamEvent
from core_harness import Addon

from symphony_server.voice_turns import VoiceLink, VoiceTurn

RUN_LINK: ContextVar[VoiceLink | None] = ContextVar("osmo_run_voice_link", default=None)


class VoiceSessionNotOpen(LookupError):
    """A voice model was run for a session with no open voice connection to it."""


class ProviderNotConnected(LookupError):
    """A text or image call reached the grok namespace while the server holds no xAI key."""


class VoiceRunLink(Addon):
    """Hands one run its session's voice link (None when no voice session is open)."""

    name = "osmo_voice_link"

    def __init__(self, link: VoiceLink | None) -> None:
        self.link = link

    async def before_run(self, **payload: Any) -> None:
        del payload
        RUN_LINK.set(self.link)


class GrokRouter(BaseProvider):
    """Registered once under `grok`; ModelAccess swaps `text` when the key changes."""

    def __init__(self, namespace: str, voice_model_ids: frozenset[str]) -> None:
        self.namespace, self.voice_model_ids = namespace, voice_model_ids
        self.text: GrokProvider | None = None

    async def stream(
        self,
        model_name: str,
        messages: list[Message],
        tools: list[dict[str, Any]] = [],  # noqa: B006 - BaseProvider's signature
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        model_id = f"{self.namespace}:{model_name}"
        if model_id in self.voice_model_ids:
            link = RUN_LINK.get()
            if link is None or link.model_id != model_id:
                raise VoiceSessionNotOpen(
                    f"{model_id} is a voice model and this run has no open voice session for it"
                )
            async for event in VoiceTurn(link).stream(messages, tools):
                yield event
            return
        async for event in self.connected().stream(
            model_name,
            messages,
            tools,
            max_output_tokens,
            reasoning_effort,
            extra_headers,
        ):
            yield event

    async def generate_image(
        self, model_name: str, prompt: str, output_format: str = "png"
    ) -> tuple[bytes, str]:
        return await self.connected().generate_image(model_name, prompt, output_format)

    def connected(self) -> GrokProvider:
        if self.text is None:
            raise ProviderNotConnected(
                f"{self.namespace}: no xAI key; connect xAI in the Providers panel"
            )
        return self.text
