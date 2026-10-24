"""Symphony server: core_server's run API plus Osmo's realtime WebSocket."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from core_harness import Tool
from core_server import (
    InProcessRunBackend,
    RunExecutor,
    ServerConfig,
    build_config,
    create_router,
    install_middlewares,
)
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.staticfiles import StaticFiles

from symphony_server.protocol import ModelsEvent
from symphony_server.providers import CredentialStore, create_provider_router, with_locks
from symphony_server.session import OsmoSession
from symphony_server.settings import Settings
from symphony_server.tools import StockTool, WeatherTool
from symphony_server.voices import (
    PREVIEW_CLIENTS_MAX,
    PREVIEW_RATE_LIMIT,
    PREVIEW_RATE_WINDOW_S,
    PREVIEW_SAMPLE_RATE,
    VoiceCatalog,
    KeyedRateLimiter,
    PreviewCache,
    default_selection,
    preview_error,
)

log = logging.getLogger("symphony")


def osmo_tools(client: httpx.AsyncClient) -> dict[str, Tool]:
    tools: list[Tool] = [WeatherTool(client), StockTool(client)]
    return {tool.name: tool for tool in tools}


def osmo_core_config(settings: Settings, tools: dict[str, Tool]) -> ServerConfig:
    """core_server config whose text runs share Osmo's tools and prompt."""
    return build_config(system_prompt=settings.instructions, tools=list(tools.values()))


def create_app(
    settings: Settings,
    *,
    core_config: ServerConfig,
    tools: dict[str, Tool],
    http_client: httpx.AsyncClient,
) -> FastAPI:
    credentials = CredentialStore(
        settings.credentials_file,
        settings.credentials_key,
        env={"XAI_API_KEY": settings.xai_api_key} if settings.xai_api_key else {},
    )
    catalog = VoiceCatalog(http_client, credentials)
    previews = PreviewCache(settings.realtime_url, credentials, settings.preview_cache_dir, catalog=catalog)
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
        """The picker's registry before any socket is open (the same payload as the `models` event)."""
        models = with_locks(await catalog.refresh(), credentials)
        return ModelsEvent(models=list(models), selection=default_selection(models, settings.voice))

    @app.get("/voice-preview")
    async def voice_preview(request: Request, model: str = "", voice: str = "") -> Response:
        """A cached ~2 s PCM16 mono sample of `voice` on `model`; same checks as the `voice.preview` event."""
        refused = preview_error(await catalog.refresh(), model, voice)
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
        await OsmoSession(websocket, settings, tools, previews, credentials, catalog).run()

    if settings.static_dir is not None and settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
