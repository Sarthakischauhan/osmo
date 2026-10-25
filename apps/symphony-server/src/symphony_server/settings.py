from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from symphony_server.infisical_client import DEFAULT_CACHE_TTL_S, DEFAULT_SITE_URL, InfisicalConfig

DEFAULT_REALTIME_URL = "wss://api.x.ai/v1/realtime?model=grok-voice-latest"
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
- get_weather for forecasts, get_stock_history for tickers, web_search for anything current (places, news, facts).
"""


@dataclass(frozen=True)
class Settings:
    xai_api_key: str | None
    realtime_url: str = DEFAULT_REALTIME_URL
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

    @classmethod
    def from_env(cls) -> Settings:
        static = os.getenv("OSMO_STATIC_DIR")
        previews = os.getenv("OSMO_PREVIEW_CACHE_DIR")
        return cls(
            xai_api_key=os.getenv("XAI_API_KEY") or None,
            realtime_url=os.getenv("XAI_REALTIME_URL", DEFAULT_REALTIME_URL),
            voice=os.getenv("OSMO_VOICE", "eve"),
            static_dir=Path(static) if static else None,
            preview_cache_dir=Path(previews) if previews else None,
            credentials_file=Path(os.getenv("OSMO_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser(),
            credentials_key=os.getenv("OSMO_CREDENTIALS_KEY") or None,
            secret_store="infisical" if infisical_selected() else "file",
            infisical=infisical_from_env() if infisical_selected() else None,
        )


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
