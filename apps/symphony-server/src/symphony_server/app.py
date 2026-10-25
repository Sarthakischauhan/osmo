"""Symphony server: core_server's run API plus Osmo's voice WebSocket.

Every model call goes through core_ai package calls: the VoiceRegistry for voice
sessions and previews, the ModelRegistry for the harness runs behind `/api/runs`,
and image generation. Both get their keys from the SecretStore (ModelAccess).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from core_ai import ModelRegistry
from core_ai.voice import VoiceRegistry
from core_harness import Tool
from core_server import (
    RunContext,
    InProcessRunBackend,
    RunExecutor,
    ServerConfig,
    build_config,
    create_router,
    install_middlewares,
)
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.staticfiles import StaticFiles

from symphony_server.canvas import CreateImageTool, RenderWidgetTool, UpdateWidgetTool, WidgetBoard
from symphony_server.memory import MemoryPersistence
from symphony_server.model_access import ModelAccess
from symphony_server.protocol import ModelsEvent
from symphony_server.providers import CredentialStore, create_provider_router
from symphony_server.secret_backends import open_secret_store
from symphony_server.secret_store import SecretStore
from symphony_server.settings import TOOL_CATEGORIES, Settings
from symphony_server.tools import StockTool, WeatherTool
from symphony_server.voice_bridge import VoiceBridge
from symphony_server.voice_router import VoiceRunLink
from symphony_server.voice_turns import VoiceLinks
from symphony_server.web_search import WebSearchTool
from symphony_server.voices import (
    PREVIEW_CLIENTS_MAX,
    PREVIEW_RATE_LIMIT,
    PREVIEW_RATE_WINDOW_S,
    PREVIEW_SAMPLE_RATE,
    KeyedRateLimiter,
    PreviewCache,
    SetVoiceTool,
    core_model_id,
    default_selection,
    preview_error,
)

log = logging.getLogger("symphony")


def osmo_tools(settings: Settings, client: httpx.AsyncClient, access: ModelAccess, board: WidgetBoard) -> list[Tool]:
    """The tools of every category enabled in settings (OSMO_TOOLS)."""
    tools: list[Tool] = [
        WebSearchTool(client, settings.exa_api_key),
        CreateImageTool(access.registry, board, settings.image_model),
        RenderWidgetTool(board),
        UpdateWidgetTool(board),
        SetVoiceTool(access),
        WeatherTool(client),
        StockTool(client),
    ]
    return [tool for tool in tools if TOOL_CATEGORIES[tool.name] in settings.tool_categories]


def osmo_core_config(settings: Settings, access: ModelAccess, tools: list[Tool], links: VoiceLinks) -> ServerConfig:
    """core_server config for every run: Osmo's prompt and tools on the shared registry, history in memory,
    and a VoiceRunLink addon handing each run its session's voice link so voice models answer on it."""

    def voice_link(context: RunContext) -> VoiceRunLink:
        return VoiceRunLink(links.get(context.session_id))

    return build_config(
        registry=access.registry,
        model_id=core_model_id(access.models()[0]),
        system_prompt=settings.instructions,
        tools=tools,
        persistence=MemoryPersistence(),
        # Each voice model's configured window (Settings.voice_context_limits), by run model id:
        # core_server's KeepSystemRecentCompactor compacts a run's history against the window of
        # the model that run is on, so the first turn after a switch compacts to its budget.
        context_limits={model.full_id: model.context_limit for model in access.voices.models()},
        addon_factories=[voice_link],
    )


def create_app(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient,
    secret_store: SecretStore | None = None,
    registry: ModelRegistry | None = None,
    voices: VoiceRegistry | None = None,
) -> FastAPI:
    credentials = CredentialStore(secret_store or open_secret_store(settings))
    access = ModelAccess(registry or ModelRegistry(), voices or VoiceRegistry(settings.voice_context_limits), credentials, settings.xai_base_url)
    access.connect("xai")
    board = WidgetBoard()
    links = VoiceLinks()
    core_config = osmo_core_config(settings, access, osmo_tools(settings, http_client, access, board), links)
    previews = PreviewCache(access, settings.preview_cache_dir)
    preview_limiter = KeyedRateLimiter(PREVIEW_RATE_LIMIT, PREVIEW_RATE_WINDOW_S, PREVIEW_CLIENTS_MAX)
    backend = InProcessRunBackend(RunExecutor(core_config))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await backend.shutdown()
        await http_client.aclose()

    app = FastAPI(title="symphony-server", lifespan=lifespan)
    install_middlewares(app, core_config, prefix="/api")
    app.include_router(create_router(core_config, run_backend=backend, prefix="/api"))
    app.include_router(create_provider_router(credentials, http_client))

    @app.get("/voice-models")
    async def voice_models() -> ModelsEvent:
        """The picker's models before any socket is open (the same payload as the `models` event)."""
        models = access.models()
        return ModelsEvent(models=list(models), selection=default_selection(models, settings.voice))

    @app.get("/voice-preview")
    async def voice_preview(request: Request, model: str = "", voice: str = "") -> Response:
        """A cached ~2 s PCM16 mono sample of `voice` on `model`; same checks as the `voice.preview` event."""
        refused = preview_error(access.models(), model, voice)
        if refused is not None:
            return Response(refused, status_code=400, media_type="text/plain")
        if not preview_limiter.allow(request.client.host if request.client else "unknown"):
            return Response("too many previews; try again in a few seconds", status_code=429, media_type="text/plain")
        try:
            audio = await previews.get(model, voice)
        except Exception as exc:  # reported to the caller, never fatal to the server
            log.warning("voice preview %s/%s failed: %s", model, voice, exc)
            return Response(f"preview failed: {exc}", status_code=502, media_type="text/plain")
        return Response(audio, media_type=f"audio/L16;rate={PREVIEW_SAMPLE_RATE};channels=1")

    @app.websocket("/ws")
    async def osmo_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        bridge = VoiceBridge(
            websocket,
            core_config=core_config,
            backend=backend,
            access=access,
            board=board,
            links=links,
            previews=previews,
            default_voice=settings.voice,
        )
        await bridge.run()

    if settings.static_dir is not None and settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
