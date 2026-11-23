"""Music: the local library (scan, tags, search, streaming inside its folders), the Spotify sign-in (PKCE, tokens in
the credential store, refresh, search), and the voice tools that tell the app's player what to do."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import wave
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import FakeGrok, make_client, xai_key_store
from core_harness import EventSink
from fastapi.testclient import TestClient
from mutagen.id3 import APIC, TALB, TIT2, TPE1
from mutagen.wave import WAVE
from test_session import call, next_event, say, start, tool_output

from symphony_server.file_store import FileSecretStore
from symphony_server.music import OSMO_MUSIC, MusicNow, music_tools
from symphony_server.music_library import LocalLibrary, MusicRoot, names_from_filename, parse_music_dirs
from symphony_server.protocol import MusicCommand, MusicState, Track
from symphony_server.providers import CredentialStore
from symphony_server.secret_store import DEFAULT_OWNER
from symphony_server.settings import DEFAULT_XAI_BASE_URL, Settings
from symphony_server.spotify import SPOTIFY_TOKEN_URL, SpotifyAccount, SpotifyError, pkce_challenge

JPEG = b"\xff\xd8\xff\xe0fakejpeg"


def wav(path: Path, seconds: float = 0.5, title: str | None = None, artist: str | None = None, album: str | None = None, cover: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * int(8000 * seconds))
    if title or artist or album or cover:
        audio = WAVE(path)
        audio.add_tags()
        assert audio.tags is not None
        for frame in (TIT2(encoding=3, text=title) if title else None, TPE1(encoding=3, text=artist) if artist else None, TALB(encoding=3, text=album) if album else None):
            if frame is not None:
                audio.tags.add(frame)
        if cover:
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=cover))
        audio.save()
    return path


@pytest.fixture
def music_dir(tmp_path: Path) -> Path:
    root = tmp_path / "Music"
    wav(root / "The xx" / "Coexist" / "01 Angels.wav", 1.25, title="Angels", artist="The xx", album="Coexist", cover=JPEG)
    wav(root / "The xx" / "xx" / "Crystalised.wav", title="Crystalised", artist="The xx", album="xx")
    wav(root / "Bonobo - Kerala.wav")
    wav(root / ".hidden" / "Secret.wav")
    wav(root / ".Ghost.wav")
    (root / "cover.jpg").write_bytes(JPEG)
    (root / "notes.txt").write_text("not music")
    outside = wav(tmp_path / "Elsewhere" / "Outside - Song.wav")
    (root / "linked.wav").symlink_to(outside)
    (root / "LinkedFolder").symlink_to(outside.parent)
    return root


def scanned(root: Path) -> LocalLibrary:
    library = LocalLibrary((MusicRoot(root),))
    library.scan()
    library.ready.set()
    return library


def test_music_folders_setting(tmp_path: Path) -> None:
    assert parse_music_dirs(None, Path("~/Music")) == (MusicRoot(Path("~/Music").expanduser()),)
    assert parse_music_dirs("", Path("~/Music")) == ()
    assert parse_music_dirs(f"{tmp_path}/a{os.pathsep} {tmp_path}/b ", Path("x")) == (MusicRoot(tmp_path / "a"), MusicRoot(tmp_path / "b"))


def test_names_come_from_the_file_name_when_there_are_no_tags() -> None:
    assert names_from_filename(Path("Music/03 - Bonobo - Kerala.flac")) == ("Kerala", "Bonobo")
    assert names_from_filename(Path("Music/Moby/Porcelain.mp3")) == ("Porcelain", "Moby")


def test_a_scan_reads_tags_and_keeps_to_real_audio_files_inside_the_folders(music_dir: Path) -> None:
    library = scanned(music_dir)
    tracks = sorted((entry.track for entry in library.files.values()), key=lambda track: track.title)
    assert [(t.title, t.artist, t.album) for t in tracks] == [("Angels", "The xx", "Coexist"), ("Crystalised", "The xx", "xx"), ("Kerala", "Bonobo", None)]
    angels = tracks[0]
    assert angels.durationMs == 1250 and angels.source == "local"
    assert angels.url == f"/api/music/local/{angels.id}" and angels.art == f"/api/music/local/{angels.id}/art"
    # Hidden files and folders, symlinks (to a file or a folder outside) and non-audio files are not in the library.
    paths = {entry.path.name for entry in library.files.values()}
    assert paths == {"01 Angels.wav", "Crystalised.wav", "Bonobo - Kerala.wav"}
    # Ids are opaque and stable across scans.
    again = scanned(music_dir)
    assert set(again.files) == set(library.files)


def test_search_needs_every_word_and_ranks_title_hits_first(music_dir: Path) -> None:
    library = scanned(music_dir)
    assert [t.title for t in library.search("the xx")] == ["Angels", "Crystalised"]
    assert [t.title for t in library.search("xx crystal")] == ["Crystalised"]
    assert [t.title for t in library.search("kerala")] == ["Kerala"]
    assert library.search("the xx kerala") == []
    assert len(library.search("", limit=2)) == 2


# The desktop app's token: the Spotify token route refuses anything without it.
APP_TOKEN = "launch-secret"
FROM_THE_APP = {"x-osmo-desktop-token": APP_TOKEN}


def music_client(music_dir: Path, handler: Any = None, client_id: str | None = None, grok: FakeGrok | None = None, app_token: str | None = None) -> TestClient:
    settings = Settings(music_dirs=(MusicRoot(music_dir),), spotify_client_id=client_id, xai_base_url=grok.base_url if grok else DEFAULT_XAI_BASE_URL, app_token=app_token)
    if handler is None:
        return make_client(settings, secret_store=xai_key_store())
    return make_client(settings, handler, secret_store=xai_key_store())


def test_the_api_streams_a_scanned_track_with_ranges_and_its_cover_and_nothing_else(music_dir: Path) -> None:
    with music_client(music_dir) as client:
        found = client.get("/api/music/library", params={"q": "angels"}).json()["tracks"]  # waits for the scan
        status = client.get("/api/music/status").json()
        assert status["localTracks"] == 3 and status["spotifyConfigured"] is False
        [angels] = found
        whole = client.get(angels["url"])
        assert whole.status_code == 200 and whole.headers["content-type"] == "audio/wav" and whole.content[:4] == b"RIFF"
        part = client.get(angels["url"], headers={"Range": "bytes=0-3"})
        assert part.status_code == 206 and part.content == b"RIFF"
        cover = client.get(angels["art"])
        assert cover.status_code == 200 and cover.content == JPEG and cover.headers["content-type"] == "image/jpeg"
        kerala = client.get("/api/music/library", params={"q": "kerala"}).json()["tracks"][0]
        assert client.get(kerala["art"]).status_code == 404  # no embedded cover
        # Only ids from the scan: no paths, no traversal.
        for route in ("/api/music/local/nope", "/api/music/local/..%2F..%2Fetc%2Fpasswd", "/api/music/local/nope/art"):
            assert client.get(route).status_code == 404

        # A file swapped for a symlink to somewhere else after the scan is refused.
        target = next(p for p in music_dir.rglob("Crystalised.wav"))
        crystalised = client.get("/api/music/library", params={"q": "crystalised"}).json()["tracks"][0]
        target.unlink()
        target.symlink_to(music_dir.parent / "Elsewhere" / "Outside - Song.wav")
        assert client.get(crystalised["url"]).status_code == 404


class FakeSpotify:
    """Spotify's token endpoint and search, as a MockTransport handler."""

    def __init__(self) -> None:
        self.token_forms: list[dict[str, list[str]]] = []
        self.searches: list[httpx.Request] = []
        self.playback_requests: list[httpx.Request] = []
        self.available_devices: list[dict[str, Any]] = [{"id": "spotify-device", "is_active": True, "is_restricted": False}]
        self.issued = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == SPOTIFY_TOKEN_URL:
            form = parse_qs(request.content.decode())
            self.token_forms.append(form)
            self.issued += 1
            body: dict[str, Any] = {"access_token": f"access-{self.issued}", "expires_in": 3600, "token_type": "Bearer"}
            if form["grant_type"] == ["authorization_code"]:
                body["refresh_token"] = "refresh-1"
            return httpx.Response(200, json=body)
        if request.url.path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": self.available_devices})
        if request.url.path.startswith("/v1/me/player/"):
            self.playback_requests.append(request)
            return httpx.Response(204)
        if request.url.path == "/v1/search":
            self.searches.append(request)
            return httpx.Response(
                200,
                json={
                    "tracks": {
                        "items": [
                            {
                                "id": "t1",
                                "uri": "spotify:track:t1",
                                "name": "Crystalised",
                                "duration_ms": 201000,
                                "artists": [{"name": "The xx"}],
                                "album": {"name": "xx", "images": [{"url": "https://i.scdn.co/640", "width": 640}, {"url": "https://i.scdn.co/64", "width": 64}]},
                            }
                        ]
                    }
                },
            )
        raise AssertionError(f"unexpected request {request.url}")


async def test_spotify_connect_controls_an_existing_device_and_no_device_fails() -> None:
    api = FakeSpotify()
    account = SpotifyAccount(
        "client-123",
        CredentialStore(FileSecretStore(None)),
        httpx.AsyncClient(transport=httpx.MockTransport(api)),
    )
    state = parse_qs(urlsplit(account.login_url("http://127.0.0.1/cb")).query)["state"][0]
    await account.finish("code", state)
    await account.playback("play", uri="spotify:track:t1")
    assert api.playback_requests[-1].method == "PUT"
    assert api.playback_requests[-1].url.path == "/v1/me/player/play"
    assert api.playback_requests[-1].url.params["device_id"] == "spotify-device"
    assert json.loads(api.playback_requests[-1].content) == {"uris": ["spotify:track:t1"]}
    await account.playback("pause")
    assert api.playback_requests[-1].url.path == "/v1/me/player/pause"
    await account.playback("volume", volume=40)
    assert api.playback_requests[-1].url.params["volume_percent"] == "40"
    api.available_devices = []
    with pytest.raises(SpotifyError, match="Open Spotify"):
        await account.playback("play", uri="spotify:track:t1")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


async def test_spotify_sign_in_uses_pkce_keeps_tokens_in_the_store_and_refreshes() -> None:
    spotify_api = FakeSpotify()
    store = FileSecretStore(None)
    clock = Clock()
    account = SpotifyAccount("client-123", CredentialStore(store), httpx.AsyncClient(transport=httpx.MockTransport(spotify_api)), clock)
    assert account.configured and not account.connected

    url = urlsplit(account.login_url("http://127.0.0.1:8787/api/music/spotify/callback"))
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert url.netloc == "accounts.spotify.com" and query["response_type"] == "code" and query["client_id"] == "client-123"
    assert query["code_challenge_method"] == "S256" and "user-modify-playback-state" in query["scope"].split()
    verifier = account.pending[query["state"]].verifier
    assert query["code_challenge"] == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == pkce_challenge(verifier)

    with pytest.raises(SpotifyError):
        await account.finish("code", "forged-state")
    await account.finish("the-code", query["state"])
    form = spotify_api.token_forms[-1]
    assert form["code_verifier"] == [verifier] and form["code"] == ["the-code"] and "client_secret" not in form
    assert account.connected
    stored = store.get(DEFAULT_OWNER, "spotify")
    assert stored is not None and "access-1" not in repr(stored)  # a SecretStr: never in a repr or log
    # The state is single use.
    with pytest.raises(SpotifyError):
        await account.finish("the-code", query["state"])

    assert await account.access_token() == ("access-1", clock.now + 3600)
    clock.now += 3600 - 30  # inside the refresh margin
    token, _ = await account.access_token()
    assert token == "access-2" and spotify_api.token_forms[-1]["grant_type"] == ["refresh_token"]
    assert spotify_api.token_forms[-1]["refresh_token"] == ["refresh-1"]
    # The refresh token is kept when Spotify does not send a new one.
    clock.now += 3600
    assert (await account.access_token())[0] == "access-3"

    [track] = await account.search("crystalised")
    assert track == Track(
        id="spotify:t1", source="spotify", title="Crystalised", artist="The xx", album="xx", durationMs=201000, art="https://i.scdn.co/64", uri="spotify:track:t1"
    )
    assert spotify_api.searches[-1].headers["Authorization"] == "Bearer access-3"
    account.logout()
    assert not account.connected and store.get(DEFAULT_OWNER, "spotify") is None


async def test_an_old_sign_in_is_refused_and_without_a_client_id_there_is_none() -> None:
    clock = Clock()
    account = SpotifyAccount("client-123", CredentialStore(FileSecretStore(None)), httpx.AsyncClient(transport=httpx.MockTransport(FakeSpotify())), clock)
    state = parse_qs(urlsplit(account.login_url("http://127.0.0.1/cb")).query)["state"][0]
    clock.now += 601
    with pytest.raises(SpotifyError, match="too old"):
        await account.finish("code", state)
    unconfigured = SpotifyAccount(None, CredentialStore(FileSecretStore(None)), httpx.AsyncClient())
    with pytest.raises(SpotifyError, match="OSMO_SPOTIFY_CLIENT_ID"):
        unconfigured.login_url("http://127.0.0.1/cb")
    with pytest.raises(SpotifyError, match="not connected"):
        await unconfigured.access_token()


def test_the_spotify_routes_sign_in_and_hand_the_app_a_token(music_dir: Path) -> None:
    spotify_api = FakeSpotify()
    with music_client(music_dir, spotify_api, client_id="client-123", app_token=APP_TOKEN) as client:
        client.headers.update(FROM_THE_APP)
        url = client.get("/api/music/spotify/login").json()["url"]
        query = parse_qs(urlsplit(url).query)
        assert query["redirect_uri"] == ["http://127.0.0.1:8787/api/music/spotify/callback"]
        assert client.get("/api/music/spotify/token").status_code == 404
        page = client.get("/api/music/spotify/callback", params={"code": "c", "state": query["state"][0]})
        assert page.status_code == 200 and "Spotify is connected" in page.text
        assert client.get("/api/music/status").json()["spotifyConnected"] is True
        assert client.get("/api/music/spotify/token").status_code == 404
        denied = client.get("/api/music/spotify/callback", params={"error": "access_denied"})
        assert denied.status_code == 400
        assert client.post("/api/music/spotify/logout").status_code == 204
        assert client.get("/api/music/status").json()["spotifyConnected"] is False


class Emitted(EventSink):
    def __init__(self) -> None:
        super().__init__()
        self.delivered: list[tuple[str, dict[str, Any]]] = []

    async def deliver(self, event_type: str, payload: dict[str, Any]) -> None:
        self.delivered.append((event_type, payload))


async def run(tool_name: str, library: LocalLibrary, spotify: SpotifyAccount, now: MusicNow, **args: Any) -> tuple[dict[str, Any], list[MusicCommand]]:
    sink = Emitted()
    tool = next(tool for tool in music_tools(library, spotify, now) if tool.name == tool_name)
    result: dict[str, Any] = json.loads(await tool.execute(sink=sink, args=args))
    await asyncio.sleep(0)
    return result, [MusicCommand.model_validate(payload) for kind, payload in sink.delivered if kind == OSMO_MUSIC]


async def test_music_play_prefers_connected_spotify_falls_back_to_files_and_controls_the_player(music_dir: Path) -> None:
    library = scanned(music_dir)
    offline = SpotifyAccount(None, CredentialStore(FileSecretStore(None)), httpx.AsyncClient())
    now = MusicNow()

    result, [command] = await run("music_play", library, offline, now, query="the xx")
    assert result["requested"] == "Angels by The xx" and result["source"] == "local" and result["queued"] == 1
    assert command.action == "play" and [t.title for t in command.tracks] == ["Angels", "Crystalised"]
    result, commands = await run("music_play", library, offline, now, query="the xx", source="spotify")
    assert commands[0].action == "handoff" and commands[0].query == "the xx" and "not connected" in result["notes"][0]
    result, commands = await run("music_play", library, offline, now, query="nothing like this")
    assert result["handoff"] == "spotify" and commands[0].action == "handoff"

    store = FileSecretStore(None)
    online = SpotifyAccount("client-123", CredentialStore(store), httpx.AsyncClient(transport=httpx.MockTransport(FakeSpotify())))
    state = parse_qs(urlsplit(online.login_url("http://127.0.0.1/cb")).query)["state"][0]
    await online.finish("code", state)
    result, [command] = await run("music_play", library, online, now, query="crystalised")
    assert result["source"] == "spotify" and command.tracks[0].uri == "spotify:track:t1"
    result, [command] = await run("music_play", library, online, now, query="crystalised", source="local")
    assert command.tracks[0].source == "local"

    result, [command] = await run("music_control", library, online, now, action="volume", volume=30)
    assert result == {"requested": "volume", "status": "pending_player_confirmation", "volume": 30} and command.action == "volume" and command.volume == 30
    assert (await run("music_control", library, online, now, action="volume"))[0]["error"].startswith("volume needs")
    assert (await run("music_control", library, online, now, action="next"))[1][0].action == "next"

    assert (await run("music_status", library, online, now))[0] == {"playing": False, "track": None}
    now.state = MusicState(type="music.state", playing=True, track=command_track(), positionMs=1000, volume=55)
    assert (await run("music_status", library, online, now))[0] == {
        "playing": True, "track": "Angels by The xx", "source": "local", "positionMs": 1000, "volume": 55
    }


def command_track() -> Track:
    return Track(id="a", source="local", title="Angels", artist="The xx", url="/api/music/local/a")


def test_the_voice_model_plays_music_in_the_app_and_hears_what_it_plays(grok: FakeGrok, music_dir: Path) -> None:
    with music_client(music_dir, grok=grok) as client, client.websocket_connect("/ws") as ws:
        start(ws, grok)
        say(grok, "Play some Bonobo.")
        call(grok, "music_play", {"query": "bonobo"}, "m1")
        command = next_event(ws, "music.command")
        assert command["action"] == "play" and command["tracks"][0]["title"] == "Kerala"
        assert tool_output(grok)["requested"] == "Kerala by Bonobo"

        # The app reports its player; the next status reads it.
        ws.send_text(json.dumps({"type": "music.state", "playing": True, "track": command["tracks"][0], "positionMs": 5000, "volume": 70}))
        say(grok, "What's playing?")
        call(grok, "music_status", {}, "m2", "r2")
        assert tool_output(grok) == {"playing": True, "track": "Kerala by Bonobo", "source": "local", "positionMs": 5000, "volume": 70}
