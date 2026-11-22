"""Music: the user's own audio files and their Spotify, played by the app and driven by the voice model.

Playback happens in the app (the sidebar player): local files stream from `GET /api/music/local/{id}` into an
audio element, and Spotify plays through its Web Playback SDK, which needs Widevine (the desktop app runs on
castLabs ECS for it; Chrome has it built in). The server finds what to play and tells the app: the voice tools
`music_play` and `music_control` send a `music.command` event, and the app reports what it is playing back with
`music.state`, which `MusicNow` keeps for `music_status`. Osmo ducks the music while it speaks (the app does it).

The local files are `music_library.LocalLibrary`; the Spotify sign-in is `spotify.SpotifyAccount`. This module has
the tools and the `/api/music` routes over both.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from core_harness import EventSink, Tool
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from symphony_server.local_access import require_same_origin
from symphony_server.music_library import MEDIA_TYPES, SEARCH_LIMIT_MAX, LocalLibrary, art_of
from symphony_server.protocol import MusicCommand, MusicState, Track
from symphony_server.spotify import SpotifyAccount, SpotifyError

log = logging.getLogger("symphony.music")

# How many results a play queues after the first (so "next" has somewhere to go).
PLAY_QUEUE = 10


# ------------------------------------------------------------ what the app plays, and what it says it plays


@dataclass
class MusicNow:
    """The last state the app reported (one user, one player)."""

    state: MusicState = field(default_factory=lambda: MusicState(type="music.state", playing=False))


OSMO_MUSIC = "osmo.music"


class PlayArgs(BaseModel):
    """music_play's arguments."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200, description="What to play: a song, artist, album or mood, as the user said it.")
    source: Literal["any", "spotify", "local"] = Field(
        default="any", description="'local' for the user's own files, 'spotify' for Spotify; 'any' tries Spotify first when connected."
    )


class ControlArgs(BaseModel):
    """music_control's arguments."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["pause", "resume", "next", "previous", "volume"]
    volume: int | None = Field(default=None, ge=0, le=100, description="0-100, with action 'volume'.")


class MusicPlayTool(Tool):
    """music_play: find tracks (Spotify, the local library or both) and play them in the app's player."""

    def __init__(self, library: LocalLibrary, spotify: SpotifyAccount) -> None:
        super().__init__(
            name="music_play",
            description="Find music and play it in the Osmo app (Spotify when connected, or the user's own files). Say what is playing in one short line.",
            parameters=PlayArgs.model_json_schema(),
        )
        self.library, self.spotify = library, spotify

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        """Find what was asked for and send the player the tracks."""
        try:
            command = PlayArgs.model_validate(args)
        except ValidationError as exc:
            return json.dumps({"error": str(exc)})
        found, notes = await self.find(command)
        if not found:
            return json.dumps({"error": "nothing found to play", "notes": notes})
        tracks = found[: PLAY_QUEUE + 1]
        await sink.emit(OSMO_MUSIC, MusicCommand(action="play", tracks=tracks).model_dump(mode="json"))
        return json.dumps({"playing": tracks[0].label(), "source": tracks[0].source, "queued": len(tracks) - 1, "notes": notes})

    async def find(self, command: PlayArgs) -> tuple[list[Track], list[str]]:
        """The tracks for a request, and notes on the sources that could not be searched."""
        notes: list[str] = []
        if command.source in ("any", "spotify"):
            if self.spotify.connected:
                try:
                    tracks = await self.spotify.search(command.query, PLAY_QUEUE + 1)
                    if tracks:
                        return tracks, notes
                except SpotifyError as exc:
                    notes.append(str(exc))
            elif command.source == "spotify":
                notes.append("Spotify is not connected: the user can connect it from the music player in the sidebar")
        if command.source in ("any", "local"):
            await self.library.ready.wait()
            return self.library.search(command.query, PLAY_QUEUE + 1), notes
        return [], notes


class MusicControlTool(Tool):
    """music_control: pause, resume, skip or set the player's volume."""

    def __init__(self) -> None:
        super().__init__(
            name="music_control",
            description="Pause, resume, skip to the next or previous track, or set the volume (0-100) of the Osmo music player.",
            parameters=ControlArgs.model_json_schema(),
        )

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        """Send the player the command."""
        try:
            command = ControlArgs.model_validate(args)
        except ValidationError as exc:
            return json.dumps({"error": str(exc)})
        if command.action == "volume" and command.volume is None:
            return json.dumps({"error": "volume needs a value from 0 to 100"})
        await sink.emit(OSMO_MUSIC, MusicCommand(action=command.action, volume=command.volume).model_dump(mode="json"))
        return json.dumps({"done": command.action, **({"volume": command.volume} if command.action == "volume" else {})})


class MusicStatusTool(Tool):
    """music_status: what the app's player plays now."""

    def __init__(self, now: MusicNow) -> None:
        super().__init__(
            name="music_status",
            description="What the Osmo music player is playing right now (track, position, volume).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        self.now = now

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        """Describe the player's state."""
        del sink, args
        state = self.now.state
        if state.track is None:
            return json.dumps({"playing": False, "track": None})
        return json.dumps(
            {"playing": state.playing, "track": state.track.label(), "source": state.track.source, "positionMs": state.positionMs, "volume": state.volume}
        )


def music_tools(library: LocalLibrary, spotify: SpotifyAccount, now: MusicNow) -> list[Tool]:
    """The music tools."""
    return [MusicPlayTool(library, spotify), MusicControlTool(), MusicStatusTool(now)]


MUSIC_TOOL_NAMES = frozenset({"music_play", "music_control", "music_status"})


# ------------------------------------------------------------ the API


class MusicStatus(BaseModel):
    """The music sources: the local folders and tracks, and whether Spotify is set up and connected."""

    localFolders: list[str]
    localTracks: int
    localReady: bool
    spotifyConfigured: bool
    spotifyConnected: bool


class LibraryResponse(BaseModel):
    """Tracks from the local library."""

    tracks: list[Track]


class LoginResponse(BaseModel):
    """Where to send the user to connect Spotify."""

    url: str


CALLBACK_PAGE = """<!doctype html><meta charset="utf-8"><title>Osmo</title>
<body style="font:14px system-ui;background:#0a0a0a;color:#ededed;display:grid;place-items:center;height:100vh;margin:0">
<p>{message} You can close this tab and go back to Osmo.</p></body>"""


def create_music_router(library: LocalLibrary, spotify: SpotifyAccount, redirect_uri: str | None) -> APIRouter:
    """The music API: the local library, its files and covers, and the Spotify connection."""
    router = APIRouter(prefix="/api/music")

    def callback_uri(request: Request) -> str:
        """The Spotify redirect URI: the configured one, else this server's callback."""
        # Spotify wants the exact registered redirect; loopback apps register http://127.0.0.1:<port>/api/music/spotify/callback.
        return redirect_uri or str(request.url_for("spotify_callback"))

    @router.get("/status")
    async def status() -> MusicStatus:
        """The music sources' status."""
        return MusicStatus(
            localFolders=[str(root.path) for root in library.roots],
            localTracks=len(library.files),
            localReady=library.ready.is_set(),
            spotifyConfigured=spotify.configured,
            spotifyConnected=spotify.connected,
        )

    @router.get("/library")
    async def search(q: str = Query("", max_length=200), limit: int = Query(10, ge=1, le=SEARCH_LIMIT_MAX)) -> LibraryResponse:
        """Search the local library."""
        await library.ready.wait()
        return LibraryResponse(tracks=library.search(q, limit))

    @router.get("/local/{track_id}")
    async def stream(track_id: str) -> FileResponse:
        """A local track's file (Range requests work, so the player can seek)."""
        entry = library.file(track_id)
        # FileResponse answers Range requests, so the player can seek.
        return FileResponse(entry.path, media_type=MEDIA_TYPES.get(entry.path.suffix.lower(), "application/octet-stream"))

    @router.get("/local/{track_id}/art")
    async def art(track_id: str) -> Response:
        """A local track's embedded cover."""
        entry = library.file(track_id)
        cover = await asyncio.to_thread(art_of, entry.path)
        if cover is None:
            raise HTTPException(404, "no cover art")
        data, media_type = cover
        return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})

    @router.post("/rescan")
    async def rescan() -> MusicStatus:
        """Scan the local folders again."""
        await asyncio.to_thread(library.scan)
        library.ready.set()
        return await status()

    @router.get("/spotify/login")
    async def login(request: Request) -> LoginResponse:
        """Start connecting Spotify (PKCE): the authorization URL."""
        try:
            return LoginResponse(url=spotify.login_url(callback_uri(request)))
        except SpotifyError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/spotify/callback", name="spotify_callback", response_class=HTMLResponse)
    async def callback(code: str | None = None, state: str | None = None, error: str | None = None) -> HTMLResponse:
        """Finish connecting Spotify, from its redirect."""
        if error is not None or code is None or state is None:
            return HTMLResponse(CALLBACK_PAGE.format(message="Spotify was not connected."), status_code=400)
        try:
            await spotify.finish(code, state)
        except SpotifyError as exc:
            log.warning("spotify sign-in failed: %s", exc)
            return HTMLResponse(CALLBACK_PAGE.format(message="Spotify was not connected."), status_code=400)
        return HTMLResponse(CALLBACK_PAGE.format(message="Spotify is connected."))

    @router.get("/spotify/token", dependencies=[Depends(require_same_origin)])
    async def token() -> dict[str, Any]:
        """A fresh Spotify access token for the app's player, and nothing else (local_access.require_same_origin)."""
        try:
            access, expires = await spotify.access_token()
        except SpotifyError as exc:
            raise HTTPException(409, str(exc)) from None
        # The Playback SDK runs in the app and needs the token itself; it is short-lived and never logged.
        return {"accessToken": access, "expiresAt": expires}

    @router.post("/spotify/logout", status_code=204)
    async def logout() -> None:
        """Disconnect Spotify."""
        spotify.logout()

    return router
