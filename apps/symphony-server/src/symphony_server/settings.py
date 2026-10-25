from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from pydantic import SecretStr

from symphony_server.infisical_client import DEFAULT_CACHE_TTL_S, DEFAULT_SITE_URL, InfisicalConfig

DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_IMAGE_MODEL = "grok:grok-imagine-image-2.0"
DEFAULT_CREDENTIALS_FILE = "~/.config/osmo/credentials.enc"

DEFAULT_INSTRUCTIONS = """You are Osmo, a voice-only assistant. The user can only talk to you; they see an orb and a canvas of widgets.

# Style
- Speak in one or two short sentences. Never read out lists, numbers tables, URLs or markup.

# Widgets
- Whenever an answer has structure (weather, choices, prices over time, a tool or mini app), call render_widget and then say one line about what is on screen, like "here are three options".
- Never describe a widget out loud in detail; the tool result tells you what was shown.
- Use options for any "pick one" moment; give each choice a short id. A tap arrives as a user message starting with [Tapped on screen].
- Use chart (line) for stock history; use card for weather or summaries; use html for calculators, mini apps or anything custom. html must be a complete, self-contained document with inline CSS and JS, dark background (#111), light text.
- To change a widget already on screen, call update_widget with its id.

# Tools
- set_voice when the user asks to change your voice, voice model or reasoning effort ("a deeper voice"): pick from the catalog by traits, then confirm in one short line.
- get_weather for forecasts, get_stock_history for tickers, web_search for anything current (places, news, facts), create_image for pictures.
"""


# Tool groups the server can enable (OSMO_TOOLS); see app.osmo_tools.
ToolCategory = Literal["search", "image", "osmo"]
DEFAULT_TOOL_CATEGORIES: tuple[ToolCategory, ...] = ("search", "image", "osmo")
# The OSMO_TOOLS category of each of Osmo's tools, by tool name.
TOOL_CATEGORIES: dict[str, ToolCategory] = {
    "web_search": "search",
    "create_image": "image",
    "render_widget": "osmo",
    "update_widget": "osmo",
    "set_voice": "osmo",
    "get_weather": "osmo",
    "get_stock_history": "osmo",
}


VOICE_CONTEXT_LIMITS_ENV = "OSMO_VOICE_CONTEXT_LIMITS"


@dataclass(frozen=True)
class Settings:
    # Required: each voice model's context window in tokens, by core_ai id. xAI documents none
    # for its realtime models, so it is configured, never guessed (OSMO_VOICE_CONTEXT_LIMITS).
    voice_context_limits: Mapping[str, int]
    # xAI's REST base; core_ai derives the realtime WebSocket from it.
    xai_base_url: str = DEFAULT_XAI_BASE_URL
    voice: str = "eve"
    instructions: str = DEFAULT_INSTRUCTIONS
    static_dir: Path | None = None
    preview_cache_dir: Path | None = None
    # Encrypted provider credentials (providers.py); None keeps them in memory only.
    credentials_file: Path | None = None
    credentials_key: str | None = None
    # Which SecretStore holds them (OSMO_SECRET_STORE); `infisical` needs `infisical` set.
    secret_store: Literal["file", "infisical"] = "file"
    infisical: InfisicalConfig | None = None
    tool_categories: tuple[ToolCategory, ...] = DEFAULT_TOOL_CATEGORIES
    # core_ai model id that create_image draws with.
    image_model: str = DEFAULT_IMAGE_MODEL
    # Optional Exa key for web_search; without one Exa answers at a low shared rate limit.
    exa_api_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        static = os.getenv("OSMO_STATIC_DIR")
        previews = os.getenv("OSMO_PREVIEW_CACHE_DIR")
        return cls(
            voice_context_limits=voice_context_limits(os.getenv(VOICE_CONTEXT_LIMITS_ENV)),
            xai_base_url=os.getenv("XAI_BASE_URL", DEFAULT_XAI_BASE_URL),
            voice=os.getenv("OSMO_VOICE", "eve"),
            static_dir=Path(static) if static else None,
            preview_cache_dir=Path(previews) if previews else None,
            credentials_file=Path(os.getenv("OSMO_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser(),
            credentials_key=os.getenv("OSMO_CREDENTIALS_KEY") or None,
            secret_store="infisical" if infisical_selected() else "file",
            infisical=infisical_from_env() if infisical_selected() else None,
            tool_categories=tool_categories(os.getenv("OSMO_TOOLS")),
            image_model=os.getenv("OSMO_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
            exa_api_key=os.getenv("EXA_API_KEY") or None,
        )


def voice_context_limits(value: str | None) -> dict[str, int]:
    """OSMO_VOICE_CONTEXT_LIMITS: comma-separated ``model_id=tokens``, e.g.
    ``grok:grok-voice-latest=N,grok:grok-voice-3.0=N``. Required; VoiceRegistry checks that
    every voice model has one."""
    if not value:
        raise ValueError(
            f"{VOICE_CONTEXT_LIMITS_ENV} is required: set each voice model's context window in tokens, "
            "e.g. grok:grok-voice-latest=N,grok:grok-voice-3.0=N (xAI publishes no default)"
        )
    limits: dict[str, int] = {}
    for entry in (part.strip() for part in value.split(",") if part.strip()):
        model_id, sep, tokens = entry.rpartition("=")
        if not sep or not model_id or not tokens.isdigit() or int(tokens) <= 0:
            raise ValueError(f"{VOICE_CONTEXT_LIMITS_ENV}: {entry!r} is not model_id=<positive tokens>")
        limits[model_id] = int(tokens)
    return limits


def tool_categories(value: str | None) -> tuple[ToolCategory, ...]:
    """OSMO_TOOLS: comma-separated categories; unset means all of them."""
    if value is None:
        return DEFAULT_TOOL_CATEGORIES
    known: tuple[ToolCategory, ...] = get_args(ToolCategory)
    picked: list[ToolCategory] = []
    for name in (part.strip() for part in value.split(",") if part.strip()):
        if name == "browser":
            raise ValueError("OSMO_TOOLS: the browser category has no tools yet (there is no connect_browser)")
        category = next((c for c in known if c == name), None)
        if category is None:
            raise ValueError(f"OSMO_TOOLS: unknown category {name!r}; choose from {', '.join(known)}")
        picked.append(category)
    return tuple(picked)


INFISICAL_REQUIRED = ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID", "INFISICAL_ENV")


def infisical_selected() -> bool:
    choice = os.getenv("OSMO_SECRET_STORE") or "file"
    if choice not in ("file", "infisical"):
        raise ValueError(f"OSMO_SECRET_STORE must be 'file' or 'infisical', not {choice!r}")
    return choice == "infisical"


def infisical_from_env() -> InfisicalConfig:
    missing = [name for name in INFISICAL_REQUIRED if not os.getenv(name)]
    if missing:
        raise ValueError(f"OSMO_SECRET_STORE=infisical needs {', '.join(missing)}")
    return InfisicalConfig(
        client_id=os.environ["INFISICAL_CLIENT_ID"],
        client_secret=SecretStr(os.environ["INFISICAL_CLIENT_SECRET"]),
        project_id=os.environ["INFISICAL_PROJECT_ID"],
        environment=os.environ["INFISICAL_ENV"],
        site_url=os.getenv("INFISICAL_SITE_URL") or DEFAULT_SITE_URL,
        cache_ttl_s=float(os.getenv("INFISICAL_CACHE_TTL_S") or DEFAULT_CACHE_TTL_S),
    )
