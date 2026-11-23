"""Spotify OAuth PKCE, search and Connect control of the user's existing Spotify device.

Tokens stay in the encrypted credential store and never reach the browser renderer.
Connect playback needs Spotify Premium and a controllable client device.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from symphony_server.music_library import SEARCH_LIMIT_MAX
from symphony_server.protocol import Track
from symphony_server.providers import CredentialStore, TokenResponse, TokenSet, token_auth

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
# What the Web Playback SDK and playback control need, and nothing else.
SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state"
SPOTIFY_CREDENTIAL = "spotify"
# An access token this close to expiry is refreshed before it is handed out.
TOKEN_MARGIN_S = 60.0
# A sign-in that has not come back by then is forgotten.
LOGIN_TTL_S = 600.0


class PendingLogin(BaseModel):
    """A Spotify sign-in under way: its PKCE verifier, redirect URI and start time."""

    verifier: str
    redirect_uri: str
    created: float


class SpotifyArtist(BaseModel):
    """A track's artist."""

    model_config = ConfigDict(extra="ignore")
    name: str


class SpotifyImage(BaseModel):
    """An album cover in one size."""

    model_config = ConfigDict(extra="ignore")
    url: str
    width: int | None = None


class SpotifyAlbum(BaseModel):
    """A track's album."""

    model_config = ConfigDict(extra="ignore")
    name: str
    images: list[SpotifyImage] = []


class SpotifyTrack(BaseModel):
    """A Spotify track as search returns it."""

    model_config = ConfigDict(extra="ignore")
    id: str
    uri: str
    name: str
    duration_ms: int | None = None
    artists: list[SpotifyArtist] = []
    album: SpotifyAlbum | None = None

    def track(self) -> Track:
        """The track as Osmo's player takes it."""
        images = sorted(self.album.images if self.album else [], key=lambda image: image.width or 0)
        # The smallest cover that is still sharp at 64 px (the sidebar shows 32 px).
        art = next((image.url for image in images if (image.width or 0) >= 64), images[-1].url if images else None)
        return Track(
            id=f"spotify:{self.id}",
            source="spotify",
            title=self.name,
            artist=", ".join(artist.name for artist in self.artists),
            album=self.album.name if self.album else None,
            durationMs=self.duration_ms,
            art=art,
            uri=self.uri,
        )


class SpotifyPage(BaseModel):
    """A page of tracks."""

    model_config = ConfigDict(extra="ignore")
    items: list[SpotifyTrack] = []


class SpotifySearch(BaseModel):
    """Spotify's search reply."""

    model_config = ConfigDict(extra="ignore")
    tracks: SpotifyPage = SpotifyPage()


class SpotifyError(Exception):
    """Spotify said no (or could not be reached); the message is safe to show."""


def pkce_challenge(verifier: str) -> str:
    """The S256 PKCE challenge of a verifier."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class SpotifyAccount:
    """The user's Spotify sign-in (authorization code with PKCE) and the Web API calls Osmo makes with it."""

    def __init__(
        self, client_id: str | None, credentials: CredentialStore, http: httpx.AsyncClient, clock: Callable[[], float] = time.time
    ) -> None:
        self.client_id, self.credentials, self.http, self.clock = client_id, credentials, http, clock
        self.pending: dict[str, PendingLogin] = {}
        self.refreshing = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """Whether a Spotify client id is set."""
        return bool(self.client_id)

    def tokens(self) -> TokenSet | None:
        """The stored Spotify tokens, or None."""
        stored = self.credentials.stored(SPOTIFY_CREDENTIAL)
        if stored is None:
            return None
        try:
            return TokenSet.model_validate_json(stored.secret.get_secret_value())
        except ValidationError:
            return None

    @property
    def connected(self) -> bool:
        """Whether Spotify is set up and signed in."""
        return self.configured and self.tokens() is not None

    def login_url(self, redirect_uri: str) -> str:
        """Start a sign-in: the authorization URL to open (PKCE, with a one-time state)."""
        if not self.client_id:
            raise SpotifyError("set OSMO_SPOTIFY_CLIENT_ID to your Spotify app's client id first")
        now = self.clock()
        self.pending = {state: login for state, login in self.pending.items() if now - login.created < LOGIN_TTL_S}
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        self.pending[state] = PendingLogin(verifier=verifier, redirect_uri=redirect_uri, created=now)
        query = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": pkce_challenge(verifier),
            "scope": SPOTIFY_SCOPES,
            "state": state,
        }
        return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(query)}"

    async def finish(self, code: str, state: str) -> None:
        """Finish a sign-in: trade the code for tokens and store them."""
        login = self.pending.pop(state, None)
        if login is None or not self.client_id or self.clock() - login.created >= LOGIN_TTL_S:
            raise SpotifyError("this Spotify sign-in is unknown or too old; start it again from Osmo")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": login.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": login.verifier,
        }
        self.save((await self.token_call(form)).token_set(issued_at=self.clock()))

    async def token_call(self, form: dict[str, str]) -> TokenResponse:
        """POST to Spotify's token endpoint; SpotifyError for a failure."""
        try:
            response = await self.http.post(SPOTIFY_TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise SpotifyError(f"could not reach Spotify: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SpotifyError(f"Spotify refused the sign-in (HTTP {response.status_code})")
        try:
            return TokenResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            raise SpotifyError("Spotify sent an unexpected token response") from None

    def save(self, tokens: TokenSet) -> None:
        """Store tokens."""
        self.credentials.save(token_auth(SPOTIFY_CREDENTIAL, tokens))

    async def access_token(self) -> tuple[str, float]:
        """A current access token and when it expires, refreshed first when it is about to."""
        async with self.refreshing:
            tokens = self.tokens()
            if tokens is None or not self.client_id:
                raise SpotifyError("Spotify is not connected")
            if tokens.expires_at - self.clock() > TOKEN_MARGIN_S:
                return tokens.access_token.get_secret_value(), tokens.expires_at
            if tokens.refresh_token is None:
                raise SpotifyError("the Spotify sign-in expired; connect again")
            form = {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token.get_secret_value(), "client_id": self.client_id}
            fresh = (await self.token_call(form)).token_set(previous_refresh=tokens.refresh_token, issued_at=self.clock())
            self.save(fresh)
            return fresh.access_token.get_secret_value(), fresh.expires_at

    def logout(self) -> None:
        """Forget the stored tokens."""
        if self.credentials.stored(SPOTIFY_CREDENTIAL) is not None:
            self.credentials.remove(SPOTIFY_CREDENTIAL)

    async def search(self, query: str, limit: int = 10) -> list[Track]:
        """Search Spotify's tracks."""
        token, _ = await self.access_token()
        try:
            response = await self.http.get(
                f"{SPOTIFY_API}/search",
                params={"q": query, "type": "track", "limit": str(min(limit, SEARCH_LIMIT_MAX))},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise SpotifyError(f"could not reach Spotify: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SpotifyError(f"Spotify search failed (HTTP {response.status_code})")
        return [item.track() for item in SpotifySearch.model_validate(response.json()).tracks.items]

    async def playback(self, action: str, *, uri: str | None = None, volume: int | None = None) -> None:
        """Control the user's existing Spotify Connect device, never create an Osmo DRM player."""
        if action not in {"play", "pause", "resume", "next", "previous", "volume"}:
            raise SpotifyError("unknown Spotify playback action")
        token, _ = await self.access_token()
        headers = {"Authorization": f"Bearer {token}"}
        # A Spotify client must already be open. A restricted or missing device cannot be controlled.
        try:
            devices_response = await self.http.get(f"{SPOTIFY_API}/me/player/devices", headers=headers)
        except httpx.HTTPError as exc:
            raise SpotifyError(f"could not reach Spotify: {type(exc).__name__}") from None
        if devices_response.status_code != 200:
            raise SpotifyError(f"could not retrieve Spotify devices (HTTP {devices_response.status_code})")
        devices = devices_response.json().get("devices", [])
        device = next((d for d in devices if d.get("is_active") and not d.get("is_restricted")), None)
        if device is None:
            device = next((d for d in devices if d.get("id") and not d.get("is_restricted")), None)
        if device is None or not device.get("id"):
            raise SpotifyError("Open Spotify on your computer or phone, then retry playback in Osmo")
        device_id = str(device["id"])
        endpoints = {
            "play": ("PUT", "/me/player/play"),
            "resume": ("PUT", "/me/player/play"),
            "pause": ("PUT", "/me/player/pause"),
            "next": ("POST", "/me/player/next"),
            "previous": ("POST", "/me/player/previous"),
            "volume": ("PUT", "/me/player/volume"),
        }
        method, endpoint = endpoints[action]
        params: dict[str, str] = {"device_id": device_id}
        if volume is not None and action == "volume":
            params["volume_percent"] = str(max(0, min(100, volume)))
        body = {"uris": [uri]} if action == "play" and uri else None
        try:
            response = await self.http.request(
                method, f"{SPOTIFY_API}{endpoint}", params=params, headers=headers, json=body
            )
        except httpx.HTTPError as exc:
            raise SpotifyError(f"could not control Spotify: {type(exc).__name__}") from None
        if response.status_code not in (200, 202, 204):
            if response.status_code in (403, 404):
                raise SpotifyError("Spotify playback needs Premium and an available Spotify device")
            raise SpotifyError(f"Spotify playback failed (HTTP {response.status_code})")
