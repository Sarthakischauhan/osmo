"""Vaults and the one door to their files on disk: which vaults there are, and the checked path of a note inside one.

A vault is a named root folder (OSMO_VAULTS). Every path the tools, the editor or the watcher hand in is a
path *relative to that root*, and `resolve_note` is the one door to the disk: it rejects absolute paths, `..`
that climbs out, anything but a `.md` file, and any path whose real location (symlinks followed) is outside the
root. Then `open_note` opens it the same way it was checked, at open time: from the vault root down, one folder at
a time relative to the last (dir_fd), every component with O_NOFOLLOW, so a symlink swapped in after the check is
refused; and it refuses a note with more than one hard link (st_nlink > 1), which could be a file outside the vault.
"""

from __future__ import annotations

import errno
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

NOTE_SUFFIX = ".md"
# The largest note Osmo reads whole or writes (bytes); bigger files are indexed by their first part only.
NOTE_BYTES_MAX = 1_000_000
# Folders a vault walk never enters (Obsidian's settings, git, trash).
SKIPPED_DIRS = frozenset({".git", ".obsidian", ".trash", "node_modules"})

# Every POSIX open has O_NOFOLLOW and dir_fd; Windows has neither (and resolve_note has checked the path already).
NOFOLLOW = os.O_NOFOLLOW if os.name == "posix" else 0
DIRECTORY = os.O_DIRECTORY if os.name == "posix" else 0
WALKS_BY_FD = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd

WriteMode = Literal["create", "replace", "append"]


class VaultError(Exception):
    """A path or note the vault refuses; the message is safe to show the model and the user."""


@dataclass(frozen=True)
class Vault:
    """A named vault: a root folder of markdown notes."""

    name: str
    root: Path


def parse_vaults(value: str | None, default: Path) -> tuple[Vault, ...]:
    """OSMO_VAULTS: `name=path` pairs, comma-separated; unset means one vault `notes` at `default`; empty means none."""
    if value is None:
        return (Vault("notes", default.expanduser()),)
    vaults: list[Vault] = []
    for part in (piece.strip() for piece in value.split(",") if piece.strip()):
        name, sep, path = part.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise ValueError(f"OSMO_VAULTS: {part!r} is not name=path")
        if any(v.name == name.strip() for v in vaults):
            raise ValueError(f"OSMO_VAULTS: vault {name.strip()!r} is listed twice")
        vaults.append(Vault(name.strip(), Path(path.strip()).expanduser()))
    return tuple(vaults)


def resolve_note(root: Path, relative: str) -> Path:
    """The note `relative` names inside `root`, as a real path; VaultError for anything that is not inside it."""
    if not relative or "\0" in relative:
        raise VaultError("a note path is required")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or (pure.parts and re.fullmatch(r"[A-Za-z]:", pure.parts[0])):
        raise VaultError(f"{relative!r} must be a path inside the vault, not an absolute path")
    if ".." in pure.parts:
        raise VaultError(f"{relative!r} leaves the vault")
    if pure.suffix.lower() != NOTE_SUFFIX:
        raise VaultError(f"{relative!r} is not a markdown (.md) note")
    if any(part.startswith(".") for part in pure.parts):
        raise VaultError(f"{relative!r} is inside a hidden folder or is a hidden file")
    real_root = root.resolve(strict=True)
    resolved = (real_root / pure).resolve(strict=False)
    if not resolved.is_relative_to(real_root) or resolved == real_root:
        raise VaultError(f"{relative!r} resolves outside the vault (a symlink out of it?)")
    return resolved


def relative_name(root: Path, path: Path) -> str:
    """`path`'s name relative to the vault root, with forward slashes."""
    return path.relative_to(root.resolve()).as_posix()


def ends_without_newline(fd: int) -> bool:
    """Whether appending to the open note `fd` needs a line break first (it has text and its last byte is not one)."""
    size = os.fstat(fd).st_size
    return size > 0 and os.pread(fd, 1, size - 1) != b"\n"


def open_note(root: Path, relative: str, flags: int) -> int:
    """Open the note `relative` inside `root` (checked by resolve_note) and return its descriptor.

    The walk is done again at open time, folder by folder from the root with O_NOFOLLOW, creating missing folders
    when `flags` has O_CREAT; the note must be a regular file with one link. O_TRUNC is applied only after those
    checks, so a refused note is never emptied. FileNotFoundError and FileExistsError pass through; anything else
    the vault refuses is a VaultError."""
    resolved = resolve_note(root, relative)
    truncate = bool(flags & os.O_TRUNC)
    flags = (flags & ~os.O_TRUNC) | NOFOLLOW
    try:
        fd = walk_open(root.resolve(strict=True), resolved.relative_to(root.resolve()).parts, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise VaultError(f"{relative!r} goes through a symlink") from exc
        raise
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise VaultError(f"{relative!r} is not a file")
        if status.st_nlink > 1:
            raise VaultError(f"{relative!r} has other hard links (it may be a file outside the vault)")
        if truncate:
            os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    return fd


def walk_open(real_root: Path, parts: tuple[str, ...], flags: int) -> int:
    """Open `parts` under `real_root` one component at a time, never following a symlink."""
    if not WALKS_BY_FD:
        return os.open(real_root.joinpath(*parts), flags, 0o644)
    folder = os.open(real_root, os.O_RDONLY | DIRECTORY | NOFOLLOW)
    try:
        for part in parts[:-1]:
            if flags & os.O_CREAT:
                try:
                    os.mkdir(part, 0o755, dir_fd=folder)
                except FileExistsError:
                    pass
            inner = os.open(part, os.O_RDONLY | DIRECTORY | NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = inner
        return os.open(parts[-1], flags, 0o644, dir_fd=folder)
    finally:
        os.close(folder)
