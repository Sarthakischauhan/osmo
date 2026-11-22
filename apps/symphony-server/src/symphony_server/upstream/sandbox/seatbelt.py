"""Run shell commands under a macOS Seatbelt (``sandbox-exec``) profile.

The profile starts from ``allow default`` and takes away what an agent's shell must not
have:

- **Writes** only inside the policy's ``writable`` directories (the run's workspace, its
  home and temp dirs) and a few character devices.
- **Reads** of the user's home directory are denied, except the ``readable`` toolchain
  directories (``~/.cargo``, ``~/.bun``, ...) and the writable directories. Secrets in
  ``~/.ssh``, ``~/.config/gh``, ``~/.aws``, browser profiles and app data stay hidden.
- **The keychain**, **AppleEvents** and **LaunchServices** are unreachable, and
  ``osascript``, ``open`` and ``launchctl`` cannot be executed: each of those could ask
  an unsandboxed process to run something on the sandbox's behalf.
- **Network** is allowed unless the policy turns it off (package installs need it).

The child gets a minimal environment: no inherited secrets, ``HOME`` and ``TMPDIR``
pointing at the sandbox's own directories. Children inherit the sandbox; a copied Apple
binary is killed by code signing, not run outside it.

Seatbelt is deprecated as a public API but is what macOS's own services and other agent
sandboxes use; it is the only kernel-enforced option without a VM.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
OUTPUT_MAX = 30_000

# Toolchains and caches under $HOME that builds read but that hold no credentials.
DEFAULT_TOOLCHAIN_DIRS = (
    ".bun",
    ".cargo",
    ".rustup",
    ".nvm",
    ".pyenv",
    ".rbenv",
    ".local/bin",
    ".local/share/uv",
    ".local/share/pnpm",
    ".deno",
    "go",
    ".sdkman",
    ".volta",
    ".asdf",
)
# Never readable, even if a readable directory contains them.
DEFAULT_HIDDEN = (".ssh", ".gnupg", ".aws", ".config/gh", ".netrc", ".docker/config.json", ".kube", "Library/Keychains")

_DENIED_EXECUTABLES = ("/usr/bin/osascript", "/usr/bin/open", "/bin/launchctl", "/usr/bin/security", "/usr/bin/sudo")
_DENIED_SERVICES = (
    "com.apple.SecurityServer",
    "com.apple.securityd.xpc",
    "com.apple.security.agent",
    "com.apple.coreservices.launchservicesd",
    "com.apple.coreservices.appleevents",
)
_WRITABLE_DEVICES = ("/dev/null", "/dev/zero", "/dev/tty", "/dev/random", "/dev/urandom", "/dev/dtracehelper")


def sandbox_available() -> bool:
    return sys.platform == "darwin" and Path(SANDBOX_EXEC).exists()


def _real(path: Path) -> str:
    # Seatbelt matches resolved paths (/tmp is /private/tmp, /var is /private/var).
    return str(Path(path).expanduser().resolve())


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class SandboxPolicy:
    writable: tuple[Path, ...]
    readable: tuple[Path, ...] = ()
    hidden: tuple[Path, ...] = ()
    network: bool = True
    home: Path = field(default_factory=Path.home)

    @classmethod
    def for_workspace(cls, workspace: Path, scratch: tuple[Path, ...] = (), *, network: bool = True, home: Path | None = None) -> SandboxPolicy:
        home = home or Path.home()
        readable = tuple(home / name for name in DEFAULT_TOOLCHAIN_DIRS if (home / name).exists())
        return cls(
            writable=(workspace, *scratch),
            readable=readable,
            hidden=tuple(home / name for name in DEFAULT_HIDDEN),
            network=network,
            home=home,
        )

    def profile(self) -> str:
        writable = " ".join(f"(subpath {_quote(_real(path))})" for path in self.writable)
        devices = " ".join(f"(literal {_quote(device)})" for device in _WRITABLE_DEVICES)
        readable = " ".join(f"(subpath {_quote(_real(path))})" for path in (*self.readable, *self.writable))
        home = _real(self.home)
        lines = [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            f'(allow file-write* {writable} {devices} (regex #"^/dev/fd/") (regex #"^/dev/ttys[0-9]+$"))',
            f"(deny file-read* (subpath {_quote(home)}))",
            # Listing the home directory itself (path lookups, `cd ~`) is harmless; its contents are not.
            f"(allow file-read* (literal {_quote(home)}) {readable})" if readable else f"(allow file-read* (literal {_quote(home)}))",
        ]
        if self.hidden:
            lines.append("(deny file-read* " + " ".join(f"(subpath {_quote(_real(path))})" for path in self.hidden) + ")")
        lines += [
            "(deny process-exec " + " ".join(f"(literal {_quote(path)})" for path in _DENIED_EXECUTABLES) + ")",
            "(deny appleevent-send)",
            "(deny mach-lookup " + " ".join(f"(global-name {_quote(name)})" for name in _DENIED_SERVICES) + ")",
        ]
        if not self.network:
            lines.append("(deny network-outbound (remote ip))")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    truncated: bool = False

    def text(self) -> str:
        status = "timed out" if self.timed_out else f"exit code {self.exit_code}"
        clipped = "\n[output truncated]" if self.truncated else ""
        return f"{self.output}{clipped}\n[{status}]"


def sandbox_env(home: Path, tmp: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A clean environment: the host PATH and locale, and nothing else inherited."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TERM": "dumb",
        "CI": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "NO_COLOR": "1",
    }
    env.update(extra or {})
    return env


class SeatbeltSandbox:
    """Runs `/bin/bash -c <command>` under the policy's profile with a clean environment."""

    def __init__(self, policy: SandboxPolicy, env: Mapping[str, str]) -> None:
        if not sandbox_available():
            raise RuntimeError("the macOS sandbox (sandbox-exec) is not available on this system")
        self.policy = policy
        self.env = dict(env)
        self.profile = policy.profile()

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        args: tuple[str, ...] = (),
        stdin: bytes | None = None,
        timeout_s: float = 120.0,
        output_max: int = OUTPUT_MAX,
    ) -> CommandResult:
        """Run `command` with bash; `args` are its $1, $2, ... (never interpolated into the script)."""
        process = await asyncio.create_subprocess_exec(
            SANDBOX_EXEC,
            "-p",
            self.profile,
            shutil.which("bash") or "/bin/bash",
            "-c",
            command,
            "sandboxed",
            *args,
            cwd=str(cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        chunks: list[bytes] = []
        size = 0
        truncated = False

        async def read() -> None:
            nonlocal size, truncated
            assert process.stdout is not None
            while chunk := await process.stdout.read(65536):
                if size < output_max:
                    chunks.append(chunk[: output_max - size])
                truncated = truncated or size + len(chunk) > output_max
                size += len(chunk)

        async def write() -> None:
            if stdin is None or process.stdin is None:
                return
            try:
                process.stdin.write(stdin)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        try:
            await asyncio.wait_for(asyncio.gather(write(), read(), process.wait()), timeout_s)
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True
        except asyncio.CancelledError:
            _kill(process)
            raise
        # Also reaps what the command left running in the background.
        _kill(process)
        if timed_out:
            await process.wait()
        output = b"".join(chunks).decode("utf-8", errors="replace")
        return CommandResult(exit_code=process.returncode, output=output, timed_out=timed_out, truncated=truncated)


def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


__all__ = ["CommandResult", "SandboxPolicy", "SeatbeltSandbox", "sandbox_available", "sandbox_env"]
