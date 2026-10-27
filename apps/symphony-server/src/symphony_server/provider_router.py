"""The provider namespaces harness runs and images stream from (`grok`, `openai`, `vercel`, `gemini`).

One `ProviderRouter` is registered per namespace on the shared ModelRegistry, once; ModelAccess
swaps its core_ai text provider whenever the provider's credential changes, so a run always
calls the provider with the key the server holds right now."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from core_ai.providers.base import BaseProvider
from core_ai.types import Message, StreamEvent


class ProviderNotConnected(LookupError):
    """A text or image call reached a namespace whose provider has no key on the server."""


class ProviderRouter(BaseProvider):
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.text: BaseProvider | None = None

    async def stream(
        self,
        model_name: str,
        messages: list[Message],
        tools: list[dict[str, Any]] = [],  # noqa: B006 - BaseProvider's signature
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        async for event in self.connected().stream(
            model_name,
            messages,
            tools,
            max_output_tokens,
            reasoning_effort,
            extra_headers,
        ):
            yield event

    async def generate_image(self, model_name: str, prompt: str, output_format: str = "png") -> tuple[bytes, str]:
        return await self.connected().generate_image(model_name, prompt, output_format)

    def connected(self) -> BaseProvider:
        if self.text is None:
            raise ProviderNotConnected(f"{self.namespace}: no key; connect its provider in the Providers panel")
        return self.text
