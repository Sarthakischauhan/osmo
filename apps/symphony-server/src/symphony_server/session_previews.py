"""Voice previews for one browser socket: its rate limit, and keeping the mic closed while
a preview plays on the speakers."""

from __future__ import annotations

import base64
import logging
import time

from symphony_server.model_access import ModelAccess
from symphony_server.protocol import OsmoState, VoicePreviewRequest, VoicePreviewResult
from symphony_server.voices import (
    PREVIEW_RATE_LIMIT,
    PREVIEW_RATE_WINDOW_S,
    PREVIEW_SECONDS,
    PreviewCache,
    RateLimiter,
    preview_error,
)

log = logging.getLogger("symphony.voice")

# Voice previews are played only while the assistant is quiet.
PREVIEW_STATES: tuple[OsmoState, ...] = ("idle", "listening")


class SessionPreviews:
    def __init__(self, access: ModelAccess, cache: PreviewCache) -> None:
        self.access, self.cache = access, cache
        self.limiter = RateLimiter(PREVIEW_RATE_LIMIT, PREVIEW_RATE_WINDOW_S)
        # Mic audio is not forwarded until this monotonic time, so a preview playing on the
        # speakers is not heard as the user talking.
        self.mic_paused_until = 0.0

    def mic_paused(self) -> bool:
        return time.monotonic() < self.mic_paused_until

    async def play(
        self, request: VoicePreviewRequest, state: OsmoState
    ) -> VoicePreviewResult:
        """The preview audio for `request`, or why it is refused or failed."""
        refused = preview_error(self.access.models(), request.model, request.voice)
        if refused is None and state not in PREVIEW_STATES:
            refused = f"previews play only while idle or listening (now {state})"
        if refused is None and not self.limiter.allow():
            refused = "too many previews; try again in a few seconds"
        if refused is not None:
            return VoicePreviewResult(
                requestId=request.requestId,
                model=request.model,
                voice=request.voice,
                error=refused,
            )
        try:
            audio = await self.cache.get(request.model, request.voice)
        except Exception as exc:  # a failed preview is reported to the pill, never fatal to the session
            log.warning(
                "voice preview %s/%s failed: %s", request.model, request.voice, exc
            )
            return VoicePreviewResult(
                requestId=request.requestId,
                model=request.model,
                voice=request.voice,
                error=str(exc),
            )
        # The browser plays it now; keep its speakers out of the mic for that long.
        self.mic_paused_until = time.monotonic() + PREVIEW_SECONDS
        return VoicePreviewResult(
            requestId=request.requestId,
            model=request.model,
            voice=request.voice,
            audio=base64.b64encode(audio).decode("ascii"),
        )
