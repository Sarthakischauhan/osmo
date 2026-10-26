"""The voice models, the `set_voice` contract, and cached 2 s voice previews.

The picker's models are the core_ai voice catalog's realtime models (sent as the
`models` event); `set_voice` and `session.configure` are validated against the
same list, so the picker, the voice command and the server can never disagree.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from core_ai.voice import VOICE_MODELS, AudioDelta, VoiceModelSpec, VoiceClosed, VoiceConfig, VoiceSession
from core_harness import EventSink, Tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from symphony_server.model_access import ModelAccess

log = logging.getLogger("symphony.voices")

# xAI documents `reasoning.effort` as "high" (default) or "none"; low/medium stay for other providers.
Effort = Literal["none", "low", "medium", "high"]

SET_VOICE = "set_voice"
# Sink event carrying a checked `set_voice` change (SetVoiceArgs) for the voice bridge.
OSMO_VOICE = "osmo.voice"
PREVIEW_LINE = "Hi, this is how I sound. Nice to meet you."
PREVIEW_SAMPLE_RATE = 24000
PREVIEW_SECONDS = 2.0
PREVIEW_BYTES = int(PREVIEW_SAMPLE_RATE * PREVIEW_SECONDS) * 2
PREVIEW_TIMEOUT_S = 8.0
# After the preview response has ended, stop reading once no audio arrives for this long.
PREVIEW_DRAIN_S = 0.3
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


# The core_ai voice provider (also its ModelRegistry namespace) serving each Osmo provider id, and back.
CORE_NAMESPACES: dict[str, str] = {"xai": "grok", "openai": "openai", "vercel": "vercel", "gemini": "gemini"}
OSMO_PROVIDERS: dict[str, str] = {namespace: provider for provider, namespace in CORE_NAMESPACES.items()}
LATEST = "latest"


def voice_models() -> tuple[VoiceModel, ...]:
    """The picker's models: every realtime model in the core_ai voice catalog that has voices to
    pick from (a selection always names one), latest first, then in catalog order. Voices and
    reasoning efforts come from each model's VoiceModelSpec."""
    specs = [m for m in VOICE_MODELS if m.provider in OSMO_PROVIDERS and m.voices]
    return tuple(voice_model(m) for m in sorted(specs, key=lambda m: not m.id.endswith(LATEST)))


def model_labels(model_id: str) -> tuple[str, str | None]:
    """(label, ring label) from a catalog id: the leading words name the family and the rest
    (from the first token with a digit, or "latest") the version, e.g. "grok-voice-3.0" ->
    ("Grok Voice 3.0", "3.0"), "google/gemini-3.8-live" -> ("Gemini 3.8 Live", "3.8 Live"),
    "gpt-realtime" -> ("Gpt Realtime", None). An AI Gateway id's owner prefix is dropped."""
    tokens = model_id.rsplit("/", 1)[-1].split("-")
    start = next(
        (i for i, token in enumerate(tokens) if token == LATEST or any(c.isdigit() for c in token)), len(tokens)
    )
    family = " ".join(token.title() for token in tokens[:start])
    version = " ".join(token if any(c.isdigit() for c in token) else token.title() for token in tokens[start:])
    if not version:
        return family, None
    return (family if tokens[start:] == [LATEST] else f"{family} {version}"), version


def voice_model(info: VoiceModelSpec) -> VoiceModel:
    label, short_label = model_labels(info.id)
    return VoiceModel(
        id=info.id,
        provider=OSMO_PROVIDERS[info.provider],
        label=label,
        shortLabel=short_label,
        capabilities=Capabilities.model_validate(
            {"tools": True, "interrupt": True, "customVoices": False, "reasoningEffort": list(info.reasoning_efforts)}
        ),
        voices=[Voice(id=v.id, label=v.label, traits=list(v.traits)) for v in info.voices],
        defaultVoice=info.default_voice,
    )


def core_model_id(model: VoiceModel) -> str:
    """The core_ai id (`namespace:model`) behind a picker model."""
    return f"{CORE_NAMESPACES[model.provider]}:{model.id}"


SET_VOICE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": {"type": "string", "description": "Voice model id; omit to keep the current one."},
        "voice": {"type": "string", "description": "Voice id from the catalog (ids and labels)."},
        "effort": {"type": "string", "enum": ["none", "low", "medium", "high"], "description": "Only for models that list reasoning effort."},
    },
    "additionalProperties": False,
}


class SetVoiceTool(Tool):
    """Checks a voice change against the catalog and hands it to the voice bridge as an
    `osmo.voice` sink event; the bridge applies it to the live session."""

    def __init__(self, access: ModelAccess) -> None:
        super().__init__(
            name=SET_VOICE,
            description=set_voice_description(voice_models()),
            parameters=SET_VOICE_PARAMETERS,
        )
        self.access = access

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        change = parse_voice_change(args)
        error = change if isinstance(change, str) else voice_change_error(self.access.models(), change)
        if isinstance(change, str) or error is not None:
            return json.dumps({"error": error})
        configured = change.model_dump(exclude_none=True)
        await sink.emit(OSMO_VOICE, configured)
        return json.dumps({"configured": configured, "say": "Confirm the switch in one short line."})


def voice_entry(voice: Voice) -> str:
    """`id (traits)`, or just the id for a voice the catalog publishes no traits for."""
    return f"{voice.id} ({', '.join(voice.traits)})" if voice.traits else voice.id


def set_voice_description(models: tuple[VoiceModel, ...]) -> str:
    """Tool description carrying the catalog (voice ids and labels, plus traits where published)."""
    lines = []
    for model in models:
        voices = "; ".join(voice_entry(v) for v in model.voices)
        efforts = ", ".join(model.capabilities.reasoningEffort or []) or "none"
        lines.append(f"- {model.id}: voices {voices}; reasoning effort {efforts}")
    return (
        "Switch the voice, voice model or reasoning effort when the user asks (e.g. 'a deeper voice'). "
        "Choose ids only from this catalog, then confirm in one short line.\n" + "\n".join(lines)
    )


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


def parse_voice_change(args: Mapping[str, Any]) -> SetVoiceArgs | str:
    """A `set_voice` call's raw arguments as a typed change, or why they are invalid."""
    try:
        return SetVoiceArgs.model_validate(dict(args))
    except ValidationError as exc:
        return "invalid arguments: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])


def resolve_selection(models: tuple[VoiceModel, ...], change: SetVoiceArgs, current: Selection) -> Selection | str:
    """Apply a partial change to `current`; returns the new selection or an error for the model."""
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
        effort = default_effort(allowed)
    return Selection(model=model.id, voice=voice.id, effort=effort)


def voice_change_error(models: tuple[VoiceModel, ...], change: SetVoiceArgs) -> str | None:
    """Why a `set_voice` change could not apply on any model's defaults, or None. The tool has no
    session in hand, so it checks here and the bridge resolves the change against its selection."""
    outcomes = [
        resolve_selection(models, change, Selection(model=m.id, voice=m.defaultVoice, effort=None)) for m in models
    ]
    if any(isinstance(outcome, Selection) for outcome in outcomes):
        return None
    return next((outcome for outcome in outcomes if isinstance(outcome, str)), "no voice models are available")


def default_effort(allowed: list[Effort]) -> Effort:
    """Medium where offered, else the highest effort (efforts are listed lowest first; xAI defaults to high)."""
    return "medium" if "medium" in allowed else allowed[-1]


def default_selection(models: tuple[VoiceModel, ...], voice: str) -> Selection:
    """The first model with `voice` if it has one (else its default voice) and its default effort."""
    model = models[0]
    allowed = model.capabilities.reasoningEffort or []
    # Computed here too, so a locked first model (which `resolve_selection` refuses) still gets a valid effort.
    effort = default_effort(allowed) if allowed else None
    base = Selection(model=model.id, voice=model.defaultVoice, effort=effort)
    change = SetVoiceArgs(voice=voice) if any(v.id == voice for v in model.voices) else SetVoiceArgs()
    picked = resolve_selection(models, change, base)
    return picked if isinstance(picked, Selection) else base


class PreviewCache:
    """2 s PCM16 samples per (model, voice), generated once, kept in memory and optionally on disk."""

    def __init__(
        self,
        access: ModelAccess,
        directory: Path | None,
        max_entries: int = PREVIEW_CACHE_MAX,
    ) -> None:
        self.access = access
        self.directory = directory
        self.max_entries = max_entries
        # LRU of generated audio; locks only live while a key is being fetched.
        self.memory: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.waiters: dict[tuple[str, str], int] = {}

    def models(self) -> tuple[VoiceModel, ...]:
        return voice_models()

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
        """Open a short-lived voice session and keep the first ~2 s it speaks."""
        entry = find_model(self.models(), model)
        if entry is None or not self.access.connect(entry.provider):
            raise RuntimeError(f"{entry.provider if entry else model} is not connected on the server")
        config = VoiceConfig(voice=voice, instructions="Read the user's line aloud, exactly, once.", turn_detection="manual")
        audio = bytearray()
        async with asyncio.timeout(PREVIEW_TIMEOUT_S):
            session = await self.access.open_voice(entry, config)
            try:
                await session.add_message("user", PREVIEW_LINE)
                await session.respond()
                audio = await preview_audio(session)
            finally:
                await session.aclose()
        if not audio:
            raise RuntimeError("the voice model returned no audio")
        return bytes(audio[: PREVIEW_BYTES - PREVIEW_BYTES % 2])


async def preview_audio(session: VoiceSession) -> bytearray:
    """Audio of the session's one response, up to PREVIEW_BYTES. Audio is queued before the
    response ends, so once it has ended a short quiet gap means everything has been read."""
    finished = asyncio.Event()

    async def watch() -> None:
        async for _ in session.response():
            pass
        finished.set()

    watcher = asyncio.create_task(watch())
    audio = bytearray()
    events = session.events()
    try:
        while len(audio) < PREVIEW_BYTES:
            try:
                event = await asyncio.wait_for(anext(events), PREVIEW_DRAIN_S if finished.is_set() else None)
            except (TimeoutError, StopAsyncIteration):
                break
            if isinstance(event, AudioDelta):
                audio.extend(event.audio)
            elif isinstance(event, VoiceClosed):
                break
    finally:
        watcher.cancel()
    return audio
