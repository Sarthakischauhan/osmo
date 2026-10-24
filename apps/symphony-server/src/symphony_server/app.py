"""Symphony server: core_server's run API plus Orb's realtime WebSocket."""

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

from symphony_server.session import OrbSession
from symphony_server.settings import Settings
from symphony_server.tools import StockTool, WeatherTool

log = logging.getLogger("symphony")


def orb_tools(client: httpx.AsyncClient) -> dict[str, Tool]:
    tools: list[Tool] = [WeatherTool(client), StockTool(client)]
    return {tool.name: tool for tool in tools}


def orb_core_config(settings: Settings, tools: dict[str, Tool]) -> ServerConfig:
    """core_server config whose text runs share Orb's tools and prompt."""
    return build_config(system_prompt=settings.instructions, tools=list(tools.values()))


def create_app(
    settings: Settings,
    *,
    core_config: ServerConfig,
    tools: dict[str, Tool],
    http_client: httpx.AsyncClient,
) -> FastAPI:
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
    async def orb_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await OrbSession(websocket, settings, tools).run()

    if settings.static_dir is not None and settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app
