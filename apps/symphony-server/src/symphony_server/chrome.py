"""Chrome as a child process Osmo can always get rid of.

Chrome starts in its own process group (`process_group=0`), so killing the group takes its zygote,
renderers and GPU process with it. Each profile directory carries an owner file naming the server
and the group, so a server that was SIGKILLed (and could not close its browsers) leaves something
the next server can find: `reap_stale` kills the groups of dead servers and removes their profiles.
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
import tempfile
from pathlib import Path

from symphony_server.cdp import BrowserError

log = logging.getLogger("symphony.chrome")

# What chrome prints on stderr once its DevTools server is up (with --remote-debugging-port=0 it picks the port).
DEVTOOLS_LISTENING = re.compile(r"DevTools listening on (ws://\S+)")
PROFILE_PREFIX = "osmo-browser-"
# Inside a profile: "<server pid> <chrome process group>".
OWNER_FILE = "osmo-owner"


class BrowserUnavailable(BrowserError):
    pass


async def start_chrome(executable: str) -> tuple[asyncio.subprocess.Process, Path]:
    """Chrome headless on a throwaway profile, in its own process group, and that profile."""
    profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX))
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            process_group=0,
        )
    except OSError as exc:
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


def reap_stale() -> int:
    """Kill the browsers servers that no longer run left behind and remove their profiles; how many were found.

    A profile whose server is still alive is left alone (two servers can share a machine), and so is a
    group whose leader now runs something else (its pid was reused after the browser died).
    """
    reaped = 0
    for profile in Path(tempfile.gettempdir()).glob(f"{PROFILE_PREFIX}*"):
        try:
            owner, group = (int(part) for part in (profile / OWNER_FILE).read_text().split())
        except (OSError, ValueError):
            continue
        if alive(owner):
            continue
        leader = command_line(group)
        if leader is None or f"--user-data-dir={profile}" in leader:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(group, signal.SIGKILL)
        shutil.rmtree(profile, ignore_errors=True)
        reaped += 1
        log.info("removed the browser a stopped server left behind (%s)", profile)
    return reaped
