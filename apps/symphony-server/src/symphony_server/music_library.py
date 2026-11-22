"""The user's own music: audio files under the music folders (OSMO_MUSIC_DIRS; ~/Music by default) and their tags.

A track is addressed by an opaque id from the last `LocalLibrary.scan`, never by a path the client sends; serving
it (`LocalLibrary.file`) checks again that the file's real location is inside its folder and that it is not a
symlink, so a link swapped in after the scan is refused. Tags and covers come from mutagen.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mutagen
import mutagen.flac
from fastapi import HTTPException

from symphony_server.protocol import Track

log = logging.getLogger("symphony.music")

AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav"})
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}
# A scan stops after this many files (a whole disk named as a music folder must not take the server down).
LIBRARY_FILES_MAX = 50_000
SEARCH_LIMIT_MAX = 25
FILENAME_ARTIST = re.compile(r"^\s*(?P<artist>.+?)\s+-\s+(?P<title>.+?)\s*$")
TRACK_NUMBER = re.compile(r"^\d{1,3}[\s.\-_]+")


@dataclass(frozen=True)
class MusicRoot:
    path: Path


def parse_music_dirs(value: str | None, default: Path) -> tuple[MusicRoot, ...]:
    """OSMO_MUSIC_DIRS: folders separated by the OS path separator (`:` or `;`); unset means `default`; empty means none."""
    if value is None:
        return (MusicRoot(default.expanduser()),)
    return tuple(MusicRoot(Path(part.strip()).expanduser()) for part in value.split(os.pathsep) if part.strip())


@dataclass(frozen=True)
class LocalFile:
    root: Path
    path: Path
    track: Track


# ID3 frames for the easy keys, for formats mutagen has no "easy" view of (an ID3 tag in a WAV or AIFF file).
ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB"}


def first_tag(tags: Any, key: str) -> str | None:
    """The first value of an easy-tag key, or of its ID3 frame (mutagen gives lists either way), or None."""
    if tags is None:
        return None
    for name in (key, ID3_FRAMES[key]):
        if name in tags:
            values = tags[name].text if name == ID3_FRAMES[key] else tags[name]
            text = str(values[0]).strip() if values else ""
            if text:
                return text
    return None


def tags_of(path: Path) -> tuple[str | None, str | None, str | None, int | None]:
    """Title, artist, album and duration (ms) from the file's tags; Nones for what it does not say."""
    try:
        audio = mutagen.File(path, easy=True)
    except (mutagen.MutagenError, OSError, ValueError) as exc:
        log.debug("unreadable tags in %s: %s", path, exc)
        audio = None
    if audio is None:
        return None, None, None, None
    length = float(audio.info.length) if audio.info is not None and audio.info.length else 0.0
    duration = int(length * 1000) if length > 0 else None
    return first_tag(audio.tags, "title"), first_tag(audio.tags, "artist"), first_tag(audio.tags, "album"), duration


def names_from_filename(path: Path) -> tuple[str, str]:
    """`01 Artist - Title.mp3` -> (title, artist); the folder names the artist when the file does not."""
    stem = TRACK_NUMBER.sub("", path.stem)
    named = FILENAME_ARTIST.match(stem)
    if named:
        return named.group("title"), named.group("artist")
    return stem, path.parent.name


def art_of(path: Path) -> tuple[bytes, str] | None:
    """The cover embedded in the file (ID3 APIC, MP4 covr, FLAC picture): its bytes and media type."""
    try:
        audio = mutagen.File(path)
    except (mutagen.MutagenError, OSError, ValueError):
        return None
    if audio is None:
        return None
    pictures = list(audio.pictures) if isinstance(audio, mutagen.flac.FLAC) else []
    if pictures:
        return bytes(pictures[0].data), str(pictures[0].mime or "image/jpeg")
    tags = audio.tags
    if tags is None:
        return None
    for key in list(tags.keys()):
        if str(key).startswith("APIC"):
            frame = tags[key]
            return bytes(frame.data), str(frame.mime or "image/jpeg")
    if "covr" in tags and tags["covr"]:
        cover = tags["covr"][0]
        return bytes(cover), "image/png" if bytes(cover)[:4] == b"\x89PNG" else "image/jpeg"
    return None


def inside(root: Path, path: Path) -> bool:
    """Whether `path`'s real location (symlinks followed) is inside `root`'s."""
    try:
        return path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
    except OSError:
        return False


class LocalLibrary:
    """The audio files under the music folders, from the last `scan`."""

    def __init__(self, roots: tuple[MusicRoot, ...]) -> None:
        self.roots = roots
        self.files: dict[str, LocalFile] = {}
        self.ready = asyncio.Event()

    def scan(self) -> int:
        """Walk the folders (symlinked folders are not entered) and read each file's tags; how many tracks there are."""
        found: dict[str, LocalFile] = {}
        for root in self.roots:
            if not root.path.is_dir():
                continue
            for folder, dirs, names in os.walk(root.path, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(names):
                    path = Path(folder) / name
                    if name.startswith(".") or path.suffix.lower() not in AUDIO_SUFFIXES or path.is_symlink():
                        continue
                    if len(found) >= LIBRARY_FILES_MAX:
                        log.warning("the music folders hold more than %d files; the rest are left out", LIBRARY_FILES_MAX)
                        self.files = found
                        return len(found)
                    track_id = hashlib.sha256(f"{root.path}\0{path.relative_to(root.path)}".encode()).hexdigest()[:24]
                    found[track_id] = LocalFile(root.path, path, self.describe(track_id, path))
        self.files = found
        return len(found)

    @staticmethod
    def describe(track_id: str, path: Path) -> Track:
        title, artist, album, duration = tags_of(path)
        named_title, named_artist = names_from_filename(path)
        return Track(
            id=track_id,
            source="local",
            title=title or named_title,
            artist=artist or named_artist,
            album=album,
            durationMs=duration,
            art=f"/api/music/local/{track_id}/art",
            url=f"/api/music/local/{track_id}",
        )

    def search(self, query: str, limit: int = 10) -> list[Track]:
        """Tracks whose title, artist, album or file name hold every word of `query`; title hits first."""
        words = [word for word in re.split(r"\W+", query.casefold()) if word]
        scored: list[tuple[int, str, Track]] = []
        for entry in self.files.values():
            track = entry.track
            title = track.title.casefold()
            haystack = " ".join((title, track.artist.casefold(), (track.album or "").casefold(), entry.path.stem.casefold()))
            if all(word in haystack for word in words):
                score = sum(2 if word in title else 1 for word in words)
                scored.append((-score, f"{track.artist}\0{track.title}".casefold(), track))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [track for _, _, track in scored[: min(limit, SEARCH_LIMIT_MAX)]]

    def file(self, track_id: str) -> LocalFile:
        """The file of a scanned track, still inside its folder; 404 otherwise."""
        entry = self.files.get(track_id)
        if entry is None or not inside(entry.root, entry.path) or entry.path.is_symlink():
            raise HTTPException(404, "no such track")
        return entry
