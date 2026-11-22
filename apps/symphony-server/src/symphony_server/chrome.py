"""Chrome as a child process Osmo can always get rid of.

Chrome starts in its own process group (`process_group=0`), so killing the group takes its zygote,
renderers and GPU process with it. Each profile directory carries an owner file naming the server
and the group, so a server that was SIGKILLed (and could not close its browsers) leaves something
the next server can find: `reap_stale` kills the groups of dead servers and removes their profiles.

Osmo's own persistent profile (opt-in, `OSMO_BROWSER_PROFILE`) keeps its logins between sessions: it gets
the same owner file, and reaping kills a dead server's Chrome on it but never removes it.
`find_chrome` is where Osmo looks for a browser when `OSMO_BROWSER_PATH` is not set.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

from symphony_server.cdp import BrowserError

log = logging.getLogger("symphony.chrome")

# What chrome prints on stderr once its DevTools server is up (with --remote-debugging-port=0 it picks the port).
DEVTOOLS_LISTENING = re.compile(r"DevTools listening on (ws://\S+)")
PROFILE_PREFIX = "osmo-browser-"
# Inside a profile: "<server pid> <chrome process group>".
OWNER_FILE = "osmo-owner"


# Chromium browsers Osmo can drive over CDP, in the order it prefers them: installed app bundles on macOS,
# then executables on PATH (Linux, and macOS with a Homebrew chromium).
MAC_BROWSERS = (
    "Google Chrome.app/Contents/MacOS/Google Chrome",
    "Chromium.app/Contents/MacOS/Chromium",
    "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "Brave Browser.app/Contents/MacOS/Brave Browser",
)
PATH_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser")


class BrowserUnavailable(BrowserError):
    pass


def browser_candidates(platform: str, home: Path) -> list[Path]:
    """Where an installed Chromium browser's executable would be on `platform` (a `sys.platform`), before PATH."""
    if platform != "darwin":
        return []
    return [folder / bundle for folder in (Path("/Applications"), home / "Applications") for bundle in MAC_BROWSERS]


def find_chrome(
    platform: str = sys.platform,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    names: Iterable[str] = PATH_BROWSERS,
) -> str | None:
    """The first Chromium browser installed here (an executable file), or None."""
    for candidate in browser_candidates(platform, home or Path.home()):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return next((found for name in names if (found := which(name)) is not None), None)


async def start_chrome(executable: str, profile: Path | None = None, visible: bool = False) -> tuple[asyncio.subprocess.Process, Path]:
    """Chrome in its own process group on `profile` (a throwaway one when None), and the profile it uses.

    Headless unless `visible`: then it opens a normal window on the user's screen as well as streaming to the widget.
    """
    throwaway = profile is None
    profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX)) if profile is None else profile
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *([] if visible else ["--headless=new"]),
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            process_group=0,
        )
    except OSError as exc:
        if throwaway:
            shutil.rmtree(profile, ignore_errors=True)
        raise BrowserUnavailable(f"could not start the browser {executable!r}: {exc}") from exc
    (profile / OWNER_FILE).write_text(f"{os.getpid()} {process.pid}")
    return process, profile


def kill_group(process: asyncio.subprocess.Process) -> None:
    """Kill chrome and everything it started; the group may be gone already (a crash on startup)."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


async def listening_url(process: asyncio.subprocess.Process) -> str:
    """The DevTools WebSocket url chrome prints on stderr once it is up."""
    assert process.stderr is not None
    seen: list[str] = []
    while line := (await process.stderr.readline()).decode(errors="replace"):
        listening = DEVTOOLS_LISTENING.search(line)
        if listening:
            return listening.group(1)
        seen.append(line.strip())
    raise BrowserUnavailable(f"the browser exited before it was ready: {' | '.join(seen[-5:])}")


async def drain(process: asyncio.subprocess.Process) -> None:
    """Keep reading chrome's stderr so it never blocks on a full pipe."""
    assert process.stderr is not None
    while await process.stderr.readline():
        pass


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def command_line(pid: int) -> str | None:
    """The command line of process `pid`; None when there is no such process."""
    if Path("/proc").is_dir():
        try:
            text = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return None
        return text or None
    listed = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return listed.stdout.strip() or None


def reap_profile(profile: Path) -> bool:
    """Kill the Chrome a server that no longer runs left on `profile`; whether it was a dead server's.

    A profile whose server is still alive is left alone (two servers can share a machine), and so is a
    group whose leader now runs something else (its pid was reused after the browser died).
    """
    try:
        owner, group = (int(part) for part in (profile / OWNER_FILE).read_text().split())
    except (OSError, ValueError):
        return False
    if alive(owner):
        return False
    leader = command_line(group)
    if leader is None or f"--user-data-dir={profile}" in leader:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
    (profile / OWNER_FILE).unlink(missing_ok=True)
    return True


def reap_stale(persistent: Path | None = None) -> int:
    """Kill the browsers servers that no longer run left behind and remove their throwaway profiles; how many were found.

    The `persistent` profile (Osmo's own, when enabled) only loses its stale Chrome: its logins stay.
    """
    reaped = 0
    for profile in Path(tempfile.gettempdir()).glob(f"{PROFILE_PREFIX}*"):
        if reap_profile(profile):
            shutil.rmtree(profile, ignore_errors=True)
            reaped += 1
            log.info("removed the browser a stopped server left behind (%s)", profile)
    if persistent is not None and reap_profile(persistent):
        reaped += 1
        log.info("stopped the browser a stopped server left on the Osmo profile")
    return reaped
