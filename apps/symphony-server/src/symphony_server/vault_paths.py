"""Vaults and the one door to their files on disk: which vaults there are, and the checked path of a note inside one.

A vault is a named root folder (OSMO_VAULTS). Every path the tools, the editor or the watcher hand in is a
path *relative to that root*, and `resolve_note` is the one door to the disk: it rejects absolute paths, `..`
that climbs out, anything but a `.md` file, and any path whose real location (symlinks followed) is outside the
root. Writes open the file with O_NOFOLLOW, so a symlink swapped in after the check is refused too.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

NOTE_SUFFIX = ".md"
# The largest note Osmo reads whole or writes (bytes); bigger files are indexed by their first part only.
NOTE_BYTES_MAX = 1_000_000
# Folders a vault walk never enters (Obsidian's settings, git, trash).
SKIPPED_DIRS = frozenset({".git", ".obsidian", ".trash", "node_modules"})

# Every POSIX open has O_NOFOLLOW; Windows has no such flag (and resolve_note has checked the path already).
NOFOLLOW = os.O_NOFOLLOW if os.name == "posix" else 0

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


def ends_without_newline(path: Path) -> bool:
    """Whether appending to `path` needs a line break first (it has text and its last byte is not one)."""
    with path.open("rb") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            return False
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) != b"\n"
