"""Who may use the local API: Osmo's own windows on this machine, and nothing a web page or the agent can forge.

Three checks run before any route (HTTP and the voice WebSocket alike):

- Host: the request must name an allowed host (127.0.0.1, localhost, ::1, plus OSMO_ALLOWED_HOSTS). A DNS
  rebinding page reaches the server under its own name, so it is refused here.
- Origin: when a browser sends one (every cross-site request, every POST), it must be exactly the server's own origin
  as the request reached it: the same scheme, host and port as the (allowed) Host. Another local port (a dev server,
  another app on localhost) is another site; an opaque `null` origin is refused.
- Content type: a state-changing request with a body must say `application/json`. The forms and `no-cors` fetches
  another site can send without asking (text/plain, form data, an untyped Blob) never reach a route.
- The app token: the desktop app makes a secret per launch and hands it to the server on stdin (never in an
  environment variable other processes can read, never logged). Its windows present it as an HttpOnly cookie, its
  main process as a header or a WebSocket subprotocol. With a token set, every request but the health check needs
  it, so neither a page in the agent's browser nor a script in the agent's sandbox (both can reach 127.0.0.1) can
  use the API. Without one (the web client in development) the Host and Origin checks still apply, and what hands
  out a secret or skips an approval (the Spotify token, note saves while vault_write needs approval) is refused:
  headers such as Sec-Fetch-Site prove nothing when any local process can send them.
"""

from __future__ import annotations

import hmac
import os
import re
import sys
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOKEN_HEADER = "x-osmo-desktop-token"
TOKEN_COOKIE = "osmo_token"
# The WebSocket subprotocol that carries the token (`osmo-token.<token>`), offered beside `osmo`, the one chosen.
TOKEN_PROTOCOL_PREFIX = "osmo-token."
SOCKET_PROTOCOL = "osmo"
# Open without the token: the desktop app's check that the server on the port is its own, and the OAuth redirects,
# which arrive in the system browser (no cookie) and are bound to the sign-in that started them by their state/PKCE.
OPEN_PATHS = frozenset({"/api/health", "/api/music/spotify/callback"})
OPEN_PATH = re.compile(r"/providers/[a-z0-9_-]+/oauth/callback")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# The env var that says the token comes on stdin (set by the desktop app), and the old way of passing it.
TOKEN_STDIN_ENV = "OSMO_DESKTOP_TOKEN_STDIN"
TOKEN_ENV = "OSMO_DESKTOP_TOKEN"


def read_app_token() -> str | None:
    """The desktop app's token for this launch: one line on stdin when the app says so, else OSMO_DESKTOP_TOKEN.

    Either way it is removed from this process's environment, so the agent's sandbox and Chrome never inherit it."""
    from_stdin = os.environ.pop(TOKEN_STDIN_ENV, None) == "1"
    from_env = os.environ.pop(TOKEN_ENV, None)
    token = sys.stdin.readline().strip() if from_stdin else from_env
    return token or None


def allowed_hosts(extra: str | None) -> frozenset[str]:
    """The local hosts plus OSMO_ALLOWED_HOSTS (comma-separated host names, without ports)."""
    named = {part.strip().lower().strip("[]") for part in (extra or "").split(",") if part.strip()}
    return LOCAL_HOSTS | named


def host_name(authority: str) -> str:
    """The host of a Host header or an origin's authority, without the port or IPv6 brackets."""
    return (urlsplit(f"//{authority}").hostname or "").lower()


def own_origin(scope: Scope, host: str) -> str:
    """The origin a page served by this server has, for a request that reached it under the Host header `host`."""
    scheme = {"ws": "http", "wss": "https"}.get(scope["scheme"], scope["scheme"])
    return f"{scheme}://{host}".lower()


def offered_token(headers: Headers, protocols: Iterable[str]) -> str:
    """The token a request carries: the header, the cookie, or a WebSocket subprotocol."""
    cookie = next((part.split("=", 1)[1] for part in headers.get("cookie", "").split("; ") if part.startswith(f"{TOKEN_COOKIE}=")), "")
    protocol = next((name[len(TOKEN_PROTOCOL_PREFIX):] for name in protocols if name.startswith(TOKEN_PROTOCOL_PREFIX)), "")
    return headers.get(TOKEN_HEADER) or cookie or protocol


def open_path(path: str) -> bool:
    """Whether `path` is open without the token (OPEN_PATHS, OPEN_PATH)."""
    return path in OPEN_PATHS or OPEN_PATH.fullmatch(path) is not None


def has_body(headers: Headers) -> bool:
    """Whether a request carries a body (a non-zero length, or chunks)."""
    return headers.get("content-length", "0") != "0" or "transfer-encoding" in headers


def json_typed(headers: Headers) -> bool:
    """Whether the body says it is JSON."""
    return headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"


def socket_protocols(headers: Headers) -> list[str]:
    """The subprotocols a WebSocket handshake offers."""
    return [name.strip() for name in headers.get("sec-websocket-protocol", "").split(",") if name.strip()]


class LocalAccess:
    """ASGI middleware enforcing the Host, Origin and app token checks (see the module docstring)."""

    def __init__(self, app: ASGIApp, *, hosts: frozenset[str], token: str | None) -> None:
        self.app, self.hosts, self.token = app, hosts, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        refusal = self.refusal(scope, headers)
        if refusal is None:
            await self.app(scope, receive, self.identified(scope, headers, send))
        elif scope["type"] == "websocket":
            # Closing before accepting makes the handshake fail with 403.
            await send({"type": "websocket.close", "code": 1008})
        else:
            status, reason = refusal
            await PlainTextResponse(reason, status_code=status)(scope, receive, send)

    def refusal(self, scope: Scope, headers: Headers) -> tuple[int, str] | None:
        """(status, reason) when the request may not go on; None when it may."""
        host = headers.get("host", "")
        if host_name(host) not in self.hosts:
            return 403, "unknown host"
        origin = headers.get("origin")
        if origin is not None and origin.lower() != own_origin(scope, host):
            return 403, "cross-site request"
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS and has_body(headers) and not json_typed(headers):
            return 415, "send JSON"
        if self.token is None or open_path(scope["path"]):
            return None
        offered = offered_token(headers, socket_protocols(headers) if scope["type"] == "websocket" else ())
        if not hmac.compare_digest(offered.encode(), self.token.encode()):
            return 401, "this API is for the Osmo app"
        return None

    def identified(self, scope: Scope, headers: Headers, send: Send) -> Send:
        """`send`, echoing the token to a caller that proved it (the desktop app's check of its server)."""
        token = self.token
        if token is None or scope["type"] != "http" or not hmac.compare_digest(headers.get(TOKEN_HEADER, "").encode(), token.encode()):
            return send

        async def echo(message: Message) -> None:
            """Send `message`, adding the token and no-store to the response's headers."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Osmo-Desktop-Token"] = token
                headers["Cache-Control"] = "no-store"
            await send(message)

        return echo


def has_app_token(request: Request) -> bool:
    """Whether the server runs with an app token (so LocalAccess let this request in only with it)."""
    return isinstance(request.app.state.app_token, str)


def require_app(request: Request) -> None:
    """For routes that hand out a secret (the Spotify token) or that the agent must not use without the user's approval
    (writing notes when vault_write needs it): only the desktop app's windows, which hold the token, may. Without a
    token anything on this machine could call the route, the agent's sandbox included, so it is refused."""
    if not has_app_token(request):
        raise HTTPException(403, "this needs the Osmo desktop app")
