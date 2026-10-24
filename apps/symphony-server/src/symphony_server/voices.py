"""Voice model registry, the `set_voice` contract, and cached 2 s voice previews.

The registry is what the browser's radial picker shows (sent as the `models`
event) and what `set_voice` / `session.configure` are validated against, so
the picker, the voice command and the server can never disagree.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from symphony_server import grok_events as grok

if TYPE_CHECKING:
    from symphony_server.providers import CredentialStore

log = logging.getLogger("symphony.voices")

# xAI documents `reasoning.effort` as "high" (default) or "none"; low/medium stay for other providers.
Effort = Literal["none", "low", "medium", "high"]

SET_VOICE = "set_voice"
PREVIEW_LINE = "Hi, this is how I sound. Nice to meet you."
PREVIEW_SAMPLE_RATE = 24000
PREVIEW_SECONDS = 2.0
PREVIEW_BYTES = int(PREVIEW_SAMPLE_RATE * PREVIEW_SECONDS) * 2
PREVIEW_TIMEOUT_S = 8.0
# Previews kept in memory (LRU); disk copies are not bounded by this.
PREVIEW_CACHE_MAX = 32
# Each browser socket / HTTP client may request this many previews per window.
PREVIEW_RATE_LIMIT = 6
PREVIEW_RATE_WINDOW_S = 10.0
# HTTP clients tracked by the /voice-preview limiter (LRU).
PREVIEW_CLIENTS_MAX = 256


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capabilities(_Wire):
    tools: bool
    interrupt: bool
    customVoices: bool
    reasoningEffort: list[Effort] | None = None


class Voice(_Wire):
    id: str
    label: str
    traits: list[str]


class VoiceModel(_Wire):
    id: str
    provider: str
    label: str
    # Short, distinct name for the model ring (e.g. "Latest" / "3.0"); the full id is shown on hover/focus.
    shortLabel: str | None = None
    capabilities: Capabilities
    voices: list[Voice]
    defaultVoice: str
    # True while the provider has no credential on the server; the picker offers to connect it.
    locked: bool = False


class Selection(_Wire):
    model: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    effort: Effort | None = None


class SetVoiceArgs(_Wire):
    model: str | None = None
    voice: str | None = None
    effort: Effort | None = None


# xAI's built-in voices, exactly as documented. IDs and tone descriptions come from the voice table at
# https://docs.x.ai/developers/model-capabilities/audio/text-to-speech#voices (accents from the same
# table); labels are the `name` values in the `GET /v1/tts/voices` example response at
# https://docs.x.ai/developers/rest-api-reference/inference/voice#text-to-speech---list-voices (aurora and
# liora are not in that example, so their labels are their ids capitalised). Per
# https://docs.x.ai/developers/model-capabilities/audio/voice-agent#available-voices the same roster works
# for the Speech to Speech (realtime) API. This is the fallback; `VoiceCatalog` fetches the live list.
DOCUMENTED_VOICES: tuple[Voice, ...] = (
    Voice(id="eve", label="Eve", traits=["energetic", "upbeat", "British accent"]),
    Voice(id="ara", label="Ara", traits=["warm", "friendly"]),
    Voice(id="sal", label="Sal", traits=["smooth", "balanced"]),
    Voice(id="leo", label="Leo", traits=["authoritative", "strong", "British accent"]),
    Voice(id="rex", label="Rex", traits=["confident", "clear"]),
    Voice(id="carina", label="Carina", traits=["soft", "empathetic", "soothing"]),
    Voice(id="zagan", label="Zagan", traits=["powerful", "dramatic", "unmistakable"]),
    Voice(id="helix", label="Helix", traits=["bold", "dynamic", "adrenaline-fueled"]),
    Voice(id="orion", label="Orion", traits=["rich", "cinematic", "resonant"]),
    Voice(id="luna", label="Luna", traits=["gentle", "patient", "deeply nurturing"]),
    Voice(id="iris", label="Iris", traits=["friendly", "upbeat", "naturally charming"]),
    Voice(id="altair", label="Altair", traits=["elegant", "refined", "effortlessly premium"]),
    Voice(id="zenith", label="Zenith", traits=["sharp", "focused", "driven"]),
    Voice(id="perseus", label="Perseus", traits=["strong", "confident", "trustworthy"]),
    Voice(id="helios", label="Helios", traits=["upbeat", "energetic", "endlessly versatile"]),
    Voice(id="lux", label="Lux", traits=["grounded", "calm", "quietly wise"]),
    Voice(id="kepler", label="Kepler", traits=["inventive", "forward-thinking", "charismatic"]),
    Voice(id="rigel", label="Rigel", traits=["precise", "professional", "calmly confident", "Australian accent"]),
    Voice(id="cosmo", label="Cosmo", traits=["bright", "curious", "easy to follow"]),
    Voice(id="celeste", label="Celeste", traits=["compassionate", "confident", "reassuring"]),
    Voice(id="ursa", label="Ursa", traits=["friendly", "warm", "steadfast"]),
    Voice(id="sirius", label="Sirius", traits=["quick-witted", "clever", "playful"]),
    Voice(id="lumen", label="Lumen", traits=["warm", "articulate", "engaging"]),
    Voice(id="castor", label="Castor", traits=["charismatic", "down-to-earth", "easygoing"]),
    Voice(id="naksh", label="Naksh", traits=["warm", "thoughtful", "wise", "Indian accent"]),
    Voice(id="atlas", label="Atlas", traits=["confident", "commanding", "reassuring"]),
    Voice(id="aurora", label="Aurora", traits=["serene", "steady", "radiant"]),
    Voice(id="liora", label="Liora", traits=["calm", "grounded", "luminous"]),
)
GROK_VOICES = list(DOCUMENTED_VOICES)
GROK_CAPABILITIES = Capabilities(tools=True, interrupt=True, customVoices=False, reasoningEffort=["high", "none"])

VOICE_MODELS: tuple[VoiceModel, ...] = (
    VoiceModel(
        id="grok-voice-latest",
        provider="xai",
        label="Grok Voice",
        shortLabel="Latest",
        capabilities=GROK_CAPABILITIES,
        voices=GROK_VOICES,
        defaultVoice="eve",
    ),
    VoiceModel(
        id="grok-voice-3.0",
        provider="xai",
        label="Grok Voice 3.0",
        shortLabel="3.0",
        capabilities=GROK_CAPABILITIES,
        voices=GROK_VOICES,
        defaultVoice="eve",
    ),
)

SET_VOICE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": {"type": "string", "description": "Voice model id; omit to keep the current one."},
        "voice": {"type": "string", "description": "Voice id from the catalog; pick by traits when asked for a kind of voice."},
        "effort": {"type": "string", "enum": ["none", "low", "medium", "high"], "description": "Only for models that list reasoning effort."},
    },
    "additionalProperties": False,
}


def set_voice_description(models: tuple[VoiceModel, ...]) -> str:
    """Tool description carrying the catalog, so 'a deeper voice' can be resolved from traits."""
    lines = []
    for model in models:
        voices = "; ".join(f"{v.id} ({', '.join(v.traits)})" for v in model.voices)
        efforts = ", ".join(model.capabilities.reasoningEffort or []) or "none"
        lines.append(f"- {model.id}: voices {voices}; reasoning effort {efforts}")
    return (
        "Switch the voice, voice model or reasoning effort when the user asks (e.g. 'a deeper voice'). "
        "Choose ids only from this catalog, then confirm in one short line.\n" + "\n".join(lines)
    )


# Documented at https://docs.x.ai/developers/rest-api-reference/inference/voice#text-to-speech---list-voices
XAI_VOICES_URL = "https://api.x.ai/v1/tts/voices"
VOICE_CATALOG_TTL_S = 3600.0
VOICE_CATALOG_RETRY_S = 60.0


class ListedVoice(BaseModel):
    voice_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    language: str | None = None


class ListedVoices(BaseModel):
    voices: list[ListedVoice]


def voices_from_listing(listing: ListedVoices) -> list[Voice]:
    """The live roster with labels from xAI; traits only where the docs' voice table describes the id."""
    documented = {v.id: v for v in DOCUMENTED_VOICES}
    voices: list[Voice] = []
    for listed in listing.voices:
        voice_id = listed.voice_id.lower()
        if any(v.id == voice_id for v in voices):
            continue
        known = documented.get(voice_id)
        voices.append(Voice(id=voice_id, label=listed.name, traits=known.traits if known else []))
    return voices


class VoiceCatalog:
    """xAI's built-in voices: `GET /v1/tts/voices` with the server's xAI key, cached for an hour;
    the documented static roster until a fetch succeeds or when it fails."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        credentials: CredentialStore,
        url: str = XAI_VOICES_URL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client, self.credentials, self.url, self.clock = client, credentials, url, clock
        self.models: tuple[VoiceModel, ...] = VOICE_MODELS
        self.next_fetch = 0.0
        self.lock = asyncio.Lock()

    async def refresh(self) -> tuple[VoiceModel, ...]:
        key = self.credentials.secret("xai")
        if not key or self.clock() < self.next_fetch:
            return self.models
        async with self.lock:
            if self.clock() < self.next_fetch:
                return self.models
            voices = await self.fetch(key)
            self.next_fetch = self.clock() + (VOICE_CATALOG_TTL_S if voices else VOICE_CATALOG_RETRY_S)
            if voices:
                self.models = tuple(
                    m.model_copy(update={"voices": voices}) if m.provider == "xai" and any(v.id == m.defaultVoice for v in voices) else m
                    for m in VOICE_MODELS
                )
        return self.models

    async def fetch(self, key: str) -> list[Voice]:
        try:
            response = await self.client.get(self.url, headers={"Authorization": f"Bearer {key}"}, timeout=5.0)
            response.raise_for_status()
            return voices_from_listing(ListedVoices.model_validate_json(response.content))
        except (httpx.HTTPError, ValidationError) as exc:
            log.warning("voice list unavailable, using the documented roster: %s", type(exc).__name__)
            return []


def find_model(models: tuple[VoiceModel, ...], model_id: str) -> VoiceModel | None:
    return next((m for m in models if m.id == model_id), None)


def preview_error(models: tuple[VoiceModel, ...], model_id: str, voice_id: str) -> str | None:
    """Why (model, voice) cannot be previewed, or None when both are in the registry."""
    model = find_model(models, model_id)
    if model is None:
        return f"unknown model {model_id!r}"
    if not any(v.id == voice_id for v in model.voices):
        return f"unknown voice {voice_id!r} for {model.id}"
    return None


class RateLimiter:
    """At most `limit` hits per `window` seconds (sliding window)."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self.hits: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self.hits and now - self.hits[0] >= self.window:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


class KeyedRateLimiter:
    """One `RateLimiter` per key, keeping only the `max_keys` most recently seen keys."""

    def __init__(self, limit: int, window: float, max_keys: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self.max_keys, self.clock = limit, window, max_keys, clock
        self.limiters: OrderedDict[str, RateLimiter] = OrderedDict()

    def allow(self, key: str) -> bool:
        limiter = self.limiters.pop(key, None) or RateLimiter(self.limit, self.window, self.clock)
        self.limiters[key] = limiter
        while len(self.limiters) > self.max_keys:
            self.limiters.popitem(last=False)
        return limiter.allow()


def resolve_selection(models: tuple[VoiceModel, ...], args: Mapping[str, Any], current: Selection) -> Selection | str:
    """Apply a partial change to `current`; returns the new selection or an error for the model."""
    try:
        change = SetVoiceArgs.model_validate(dict(args))
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
    model_id = change.model or current.model
    model = find_model(models, model_id)
    if model is None:
        return f"unknown model {model_id!r}; choose one of {', '.join(m.id for m in models)}"
    if model.locked:
        return f"{model.id} is locked: connect {model.provider} first"
    model_changed = model.id != current.model
    voice_id = (change.voice or (model.defaultVoice if model_changed else current.voice)).lower()
    voice = next((v for v in model.voices if v.id == voice_id), None)
    if voice is None:
        return f"unknown voice {voice_id!r} for {model.id}; choose one of {', '.join(v.id for v in model.voices)}"
    allowed = model.capabilities.reasoningEffort or []
    if change.effort is not None and change.effort not in allowed:
        return f"{model.id} does not support reasoning effort {change.effort!r}"
    effort = change.effort if change.effort is not None else (current.effort if current.effort in allowed else None)
    if effort is None and allowed:
        effort = "medium" if "medium" in allowed else allowed[0]
    return Selection(model=model.id, voice=voice.id, effort=effort)


def default_selection(models: tuple[VoiceModel, ...], voice: str) -> Selection:
    """The first model with `voice` if it has one (else its default voice) and its default effort."""
    model = models[0]
    allowed = model.capabilities.reasoningEffort or []
    # Computed here too, so a locked first model (which `resolve_selection` refuses) still gets a valid effort.
    effort: Effort | None = ("medium" if "medium" in allowed else allowed[0]) if allowed else None
    base = Selection(model=model.id, voice=model.defaultVoice, effort=effort)
    picked = resolve_selection(models, {"voice": voice} if any(v.id == voice for v in model.voices) else {}, base)
    return picked if isinstance(picked, Selection) else base


def realtime_url_for(base: str, model: str) -> str:
    """The realtime endpoint with `?model=` set to `model`."""
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "model"] + [("model", model)]
    return urlunsplit(parts._replace(query=urlencode(query)))


class PreviewCache:
    """2 s PCM16 samples per (model, voice), generated once, kept in memory and optionally on disk."""

    def __init__(
        self,
        realtime_url: str,
        credentials: CredentialStore,
        directory: Path | None,
        max_entries: int = PREVIEW_CACHE_MAX,
        catalog: VoiceCatalog | None = None,
    ) -> None:
        self.realtime_url = realtime_url
        self.catalog = catalog
        self.credentials = credentials
        self.directory = directory
        self.max_entries = max_entries
        # LRU of generated audio; locks only live while a key is being fetched.
        self.memory: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.waiters: dict[tuple[str, str], int] = {}

    def models(self) -> tuple[VoiceModel, ...]:
        return self.catalog.models if self.catalog is not None else VOICE_MODELS

    def disk_path(self, model: str, voice: str) -> Path | None:
        if self.directory is None:
            return None
        digest = hashlib.sha256(f"{model}\0{voice}\0{PREVIEW_LINE}".encode()).hexdigest()[:16]
        return self.directory / f"{digest}.pcm"

    async def get(self, model: str, voice: str) -> bytes:
        error = preview_error(self.models(), model, voice)
        if error is not None:
            raise ValueError(error)
        key = (model, voice)
        lock = self.locks.setdefault(key, asyncio.Lock())
        self.waiters[key] = self.waiters.get(key, 0) + 1
        try:
            async with lock:
                return await self.fetch(key)
        finally:
            self.waiters[key] -= 1
            if self.waiters[key] == 0:
                del self.waiters[key]
                del self.locks[key]

    async def fetch(self, key: tuple[str, str]) -> bytes:
        model, voice = key
        cached = self.memory.get(key)
        if cached is not None:
            self.memory.move_to_end(key)
            return cached
        path = self.disk_path(model, voice)
        if path is not None and path.is_file():
            audio = path.read_bytes()
        else:
            audio = await self.generate(model, voice)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(audio)
        self.memory[key] = audio
        while len(self.memory) > self.max_entries:
            self.memory.popitem(last=False)
        return audio

    async def generate(self, model: str, voice: str) -> bytes:
        entry = find_model(self.models(), model)
        api_key = self.credentials.secret(entry.provider) if entry is not None else None
        if not api_key:
            raise RuntimeError(f"{entry.provider if entry else model} is not connected on the server")
        audio = bytearray()
        async with asyncio.timeout(PREVIEW_TIMEOUT_S):
            async with connect(
                realtime_url_for(self.realtime_url, model),
                additional_headers={"Authorization": f"Bearer {api_key}"},
                max_size=None,
            ) as upstream:
                setup: list[grok.GrokClientEvent] = [
                    grok.SessionUpdate(
                        session=grok.SessionConfig(
                            voice=voice, instructions="Read the user's line aloud, exactly, once.", tools=[]
                        )
                    ),
                    grok.ConversationItemCreate(item=grok.UserMessageItem(content=[grok.InputText(text=PREVIEW_LINE)])),
                    grok.ResponseCreate(),
                ]
                for event in setup:
                    await upstream.send(event.model_dump_json(exclude_none=True))
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            audio.extend(message)
                        else:
                            kind, chunk = preview_frame(message)
                            if kind == "done":
                                break
                            audio.extend(chunk)
                        if len(audio) >= PREVIEW_BYTES:
                            break
                except ConnectionClosed:
                    log.info("preview upstream closed early")
        if not audio:
            raise RuntimeError("the voice model returned no audio")
        return bytes(audio[: PREVIEW_BYTES - PREVIEW_BYTES % 2])


def preview_frame(text: str) -> tuple[Literal["audio", "done", "other"], bytes]:
    """A JSON upstream frame during preview: base64 `response.output_audio.delta` audio (the same
    encoding session.py decodes), `response.done`, or anything else."""
    try:
        kind = grok.EventEnvelope.model_validate_json(text).type
        if kind == "response.done":
            return "done", b""
        if kind == "response.output_audio.delta":
            return "audio", base64.b64decode(grok.OutputAudioDelta.model_validate_json(text).delta)
    except (ValidationError, ValueError):
        log.warning("unparseable preview frame")
    return "other", b""
