from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REALTIME_URL = "wss://api.x.ai/v1/realtime?model=grok-voice-latest"

DEFAULT_INSTRUCTIONS = """You are Osmo, a voice-only assistant. The user can only talk to you; they see an orb and a canvas of widgets.

# Style
- Speak in one or two short sentences. Never read out lists, numbers tables, URLs or markup.

# Widgets
- Whenever an answer has structure (weather, choices, prices over time, a tool or mini app), show a widget and then say one line about what is on screen, like "here are three options".
- Never describe a widget out loud in detail; the tool result tells you what was shown.
- Use options for any "pick one" moment; give each choice a short id. A tap arrives as a user message starting with [Tapped on screen].
- Use chart (line) for stock history; use card for weather or summaries; use html for calculators, mini apps or anything custom. html must be a complete, self-contained document with inline CSS and JS, dark background (#111), light text.
- To change a widget already on screen, update it by its id.

# Tools
- When the user asks to change your voice, voice model or reasoning effort ("a deeper voice"): pick from the catalog by traits, then confirm in one short line.
- Forecasts, stock prices and anything current (places, news, facts) come from your tools; call them, never answer those from memory.
"""


@dataclass(frozen=True)
class Settings:
    xai_api_key: str | None
    realtime_url: str = DEFAULT_REALTIME_URL
    voice: str = "eve"
    instructions: str = DEFAULT_INSTRUCTIONS
    static_dir: Path | None = None
    preview_cache_dir: Path | None = None
    # Set by OSMO_DEBUG_UPSTREAM=1: where raw upstream traffic is captured (see upstream_log).
    debug_upstream_file: Path | None = None

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
            debug_upstream_file=(
                Path(os.getenv("OSMO_DEBUG_UPSTREAM_FILE", "osmo-upstream.jsonl"))
                if os.getenv("OSMO_DEBUG_UPSTREAM") == "1"
                else None
            ),
        )
