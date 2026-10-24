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
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from symphony_server.session import OsmoSession
from symphony_server.settings import Settings
from symphony_server.tools import StockTool, WeatherTool
from symphony_server.voices import PreviewCache

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
    previews = PreviewCache(settings.realtime_url, settings.xai_api_key, settings.preview_cache_dir)
    backend = InProcessRunBackend(RunExecutor(core_config))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await backend.shutdown()
        await http_client.aclose()

    app = FastAPI(title="symphony-server", lifespan=lifespan)
    install_middlewares(app, core_config, prefix="/api")
    app.include_router(create_router(core_config, run_backend=backend, prefix="/api"))

    @app.websocket("/ws")
    async def osmo_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await OsmoSession(websocket, settings, tools, previews).run()

    if settings.static_dir is not None and settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
