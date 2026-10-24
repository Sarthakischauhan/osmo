"""FastAPI application for connection-independent harness runs."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from core_server.catalog import is_accepted_model, model_registry_response
from core_server.backend import InProcessRunBackend
from core_server.config import ServerConfig
from core_server.execution import RunExecutor
from core_server.models import (
    ModelRegistryResponse,
    RunAccepted,
    RunRequest,
    RunSubmission,
    RunStatusResponse,
)
from core_server.request_limits import RequestSizeLimitMiddleware
from core_server.runs import (
    RunBackend,
    RunCapacityError,
    RunManager,
    RunNotFoundError,
    RunState,
    parse_last_event_id,
)
from core_server.sse import encode_sse

PACKAGE_VERSION = "0.4.0"


def _validate_request(run: RunRequest, config: ServerConfig) -> None:
    if len(run.message) > config.max_message_chars:
        raise HTTPException(status_code=413, detail="Message is too large")
    history = run.conversation or []
    if len(history) > config.max_history_messages:
        raise HTTPException(status_code=413, detail="Conversation has too many messages")
    history_chars = sum(len(message.model_dump_json()) for message in history)
    if history_chars > config.max_history_chars:
        raise HTTPException(status_code=413, detail="Conversation history is too large")
    if run.model_id and not is_accepted_model(config, run.model_id):
        raise HTTPException(status_code=422, detail="Unsupported model_id")


async def _authorized_record(
    request: Request,
    config: ServerConfig,
    manager: RunBackend,
    run_id: str,
) -> RunState:
    try:
        record = await manager.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    if not await config.authorize_run(request, record.context):
        raise HTTPException(status_code=404, detail="Run not found")
    return record


def create_router(
    config: ServerConfig,
    *,
    run_backend: RunBackend,
    prefix: str = "",
) -> APIRouter:
    """Create routes for mounting in an application-owned FastAPI app.

    The host application owns middleware and lifespan. In particular, it must
    call ``run_backend.shutdown()`` when its lifespan ends.
    """
    router = APIRouter(prefix=prefix)

    @router.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "version": PACKAGE_VERSION}

    @router.get("/models", response_model=ModelRegistryResponse)
    def models() -> ModelRegistryResponse:
        return model_registry_response(config)

    @router.post(
        "/runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_run(run: RunRequest, request: Request) -> RunAccepted:
        _validate_request(run, config)
        context = await config.resolve_run_context(request, run.session_id)
        if not context.session_id.strip():
            raise HTTPException(status_code=500, detail="Run context has an empty session_id")

        try:
            record = await run_backend.submit(RunSubmission(context=context, request=run))
        except RunCapacityError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Run capacity is full",
                headers={"Retry-After": "1"},
            ) from None
        return RunAccepted(
            run_id=record.run_id,
            session_id=context.session_id,
            status=record.status,
            events_url=request.url_for(
                "stream_run_events",
                run_id=record.run_id,
            ).path,
        )

    @router.get("/runs/{run_id}", response_model=RunStatusResponse)
    async def get_run(run_id: str, request: Request) -> RunStatusResponse:
        record = await _authorized_record(request, config, run_backend, run_id)
        return record.response()

    @router.get("/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        await _authorized_record(request, config, run_backend, run_id)
        cursor = max(after, parse_last_event_id(last_event_id, run_id))

        async def event_stream() -> AsyncIterator[str]:
            async for ordinal, event in run_backend.events(run_id, after=cursor):
                yield encode_sse(event, event_id=f"{run_id}:{ordinal}")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/runs/{run_id}/cancel", response_model=RunStatusResponse)
    async def cancel_run(run_id: str, request: Request) -> RunStatusResponse:
        await _authorized_record(request, config, run_backend, run_id)
        record = await run_backend.cancel(run_id)
        return record.response()

    return router


def install_middlewares(app: FastAPI, config: ServerConfig, *, prefix: str = "") -> None:
    """Install core-server's default request-size and CORS middleware."""
    path = f"{prefix.rstrip('/')}/runs" or "/runs"
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=config.max_request_bytes, path=path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app(
    config: ServerConfig,
    *,
    run_backend: RunBackend | None = None,
    run_manager: RunManager | None = None,
) -> FastAPI:
    """Build a standalone FastAPI app; embedding apps should use ``create_router``."""
    if run_backend is not None and run_manager is not None:
        raise ValueError("pass run_backend or run_manager, not both")
    backend = (
        run_backend
        if run_backend is not None
        else InProcessRunBackend(RunExecutor(config), manager=run_manager)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await backend.shutdown()

    app = FastAPI(title="core-server", version=PACKAGE_VERSION, lifespan=lifespan)
    app.state.run_backend = backend
    install_middlewares(app, config)
    app.include_router(create_router(config, run_backend=backend))
    return app


__all__ = [
    "PACKAGE_VERSION",
    "RunRequest",
    "create_app",
    "create_router",
    "install_middlewares",
]
