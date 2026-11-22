"""Per-run git workspaces: cloned on the host, worked on in the sandbox, published from the host.

Each run's directory (keyed by its session id, so a resumed run finds it again) holds:

- ``origin.git``: a bare clone of the source, made and only ever touched by the host.
  The sandbox cannot write it.
- ``work``: the clone the agent edits, builds and commits in, inside the sandbox.
- ``home`` and ``tmp``: the sandbox's ``HOME`` and ``TMPDIR``.

Publishing never runs git inside ``work`` on the host, because a sandboxed process may
have rewritten its ``.git`` (hooks, ``core.fsmonitor``, ``url.*.insteadOf``, ...) to run
code or redirect a push the moment the host's credentialed git touches it. Instead the
sandbox writes a ``git bundle`` of the branch, the host fetches that bundle into
``origin.git`` (a bundle is data: no config, no hooks), and pushes from there with hooks
off. The pull request is opened with ``gh`` against the repository, not the checkout.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .seatbelt import CommandResult, SandboxPolicy, SeatbeltSandbox, sandbox_env

GIT_TIMEOUT_S = 600.0
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB = re.compile(r"github\.com[:/]+(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_KEY = re.compile(r"[^A-Za-z0-9_-]")


class WorkspaceError(Exception):
    """Something the agent can fix or report (bad source, nothing to publish, push refused)."""


@dataclass
class WorkspaceInfo:
    source: str
    push_url: Optional[str]
    github: Optional[str]
    base: str
    branch: str


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def origin(self) -> Path:
        return self.root / "origin.git"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def meta(self) -> Path:
        return self.root / "workspace.json"

    def info(self) -> Optional[WorkspaceInfo]:
        try:
            return WorkspaceInfo(**json.loads(self.meta.read_text()))
        except FileNotFoundError:
            return None


async def _host(*argv: str, cwd: Optional[Path] = None, timeout_s: float = GIT_TIMEOUT_S) -> CommandResult:
    """A command on the host, outside the sandbox. Only ever pointed at host-owned paths."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout_s)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return CommandResult(exit_code=None, output="", timed_out=True)
    return CommandResult(exit_code=process.returncode, output=out.decode("utf-8", errors="replace").strip())


def _ok(result: CommandResult, what: str) -> str:
    if result.timed_out:
        raise WorkspaceError(f"{what} timed out")
    if result.exit_code != 0:
        raise WorkspaceError(f"{what} failed: {result.output[-2000:]}")
    return result.output


class Workspaces:
    """Workspaces under `base`, one per key (a run's session id)."""

    def __init__(self, base: Path, *, network: bool = True, git: str = "git", gh: Optional[str] = "gh") -> None:
        """`gh` is the GitHub CLI that opens pull requests (None: push only, never open one)."""
        self.base = base.expanduser()
        self.network = network
        self.git = shutil.which(git) or git
        self.gh = shutil.which(gh) if gh else None
        self._identity: Optional[dict[str, str]] = None
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> Workspace:
        name = _KEY.sub("_", key)[:120] or "default"
        return Workspace(self.base / name)

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def identity(self) -> dict[str, str]:
        """The host user's git name and email, for commits made in the sandbox (which cannot read ~/.gitconfig)."""
        if self._identity is None:
            name = (await _host(self.git, "config", "--global", "user.name")).output
            email = (await _host(self.git, "config", "--global", "user.email")).output
            self._identity = {}
            if name:
                self._identity.update(GIT_AUTHOR_NAME=name, GIT_COMMITTER_NAME=name)
            if email:
                self._identity.update(GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
        return self._identity

    async def sandbox(self, key: str) -> tuple[Workspace, SeatbeltSandbox]:
        """The key's workspace (an empty scratch directory until `open` clones into it) and its sandbox."""
        workspace = self.get(key)
        for directory in (workspace.work, workspace.home, workspace.tmp):
            directory.mkdir(parents=True, exist_ok=True)
        policy = SandboxPolicy.for_workspace(workspace.work, (workspace.home, workspace.tmp), network=self.network)
        env = sandbox_env(workspace.home, workspace.tmp, await self.identity())
        return workspace, SeatbeltSandbox(policy, env)

    def _credentials(self) -> list[str]:
        # GitHub over https with the user's `gh` login, for this command only.
        return ["-c", "credential.helper=", "-c", f"credential.helper=!{self.gh} auth git-credential"] if self.gh else []

    async def open(self, key: str, source: str, branch: Optional[str] = None) -> WorkspaceInfo:
        """Clone `source` (owner/name, an https URL, or a local repository path) and check out a new branch.

        Opening the same source again returns the existing workspace, so a resumed run keeps its work.
        """
        async with self.lock(key):
            workspace = self.get(key)
            existing = workspace.info()
            if existing is not None:
                if existing.source != source:
                    raise WorkspaceError(f"this run's workspace already holds {existing.source}")
                return existing
            url, push_url = await self._resolve(source)
            if workspace.work.exists() and any(workspace.work.iterdir()):
                shutil.rmtree(workspace.work)
            if workspace.origin.exists():
                shutil.rmtree(workspace.origin)
            workspace.root.mkdir(parents=True, exist_ok=True)
            _ok(
                await _host(self.git, *self._credentials(), "-c", "protocol.ext.allow=never", "clone", "--bare", "--no-hardlinks", "--quiet", "--", url, str(workspace.origin)),
                f"cloning {source}",
            )
            base = _ok(await _host(self.git, "-C", str(workspace.origin), "symbolic-ref", "--short", "HEAD"), "reading the default branch")
            branch = branch or f"osmo/{secrets.token_hex(3)}"
            _ok(await _host(self.git, "check-ref-format", "--branch", branch), f"branch name {branch!r}")
            # A fresh clone of the host-owned bare repository: nothing in it was written by the sandbox yet.
            _ok(await _host(self.git, "clone", "--no-hardlinks", "--quiet", "--", str(workspace.origin), str(workspace.work)), "creating the working copy")
            _ok(await _host(self.git, "-C", str(workspace.work), "checkout", "--quiet", "-b", branch), f"creating branch {branch}")
            match = _GITHUB.search(push_url) if push_url else None
            info = WorkspaceInfo(source=source, push_url=push_url, github=match.group("slug") if match else None, base=base, branch=branch)
            workspace.meta.write_text(json.dumps(asdict(info)))
            return info

    async def _resolve(self, source: str) -> tuple[str, Optional[str]]:
        """The clone URL and the URL to push to."""
        if _SLUG.match(source) and not Path(source).expanduser().exists():
            url = f"https://github.com/{source}.git"
            return url, url
        if source.startswith("https://"):
            return source, source
        path = Path(source).expanduser()
        if path.is_dir() and (path / ".git").exists():
            remote = await _host(self.git, "-C", str(path), "remote", "get-url", "--push", "origin")
            return str(path.resolve()), remote.output if remote.exit_code == 0 and remote.output else None
        raise WorkspaceError("repo must be owner/name on GitHub, an https:// git URL, or the path of a local git repository")

    async def publish(self, key: str, title: str, body: str, *, draft: bool = False) -> str:
        """Push the workspace's branch and open (or find) its pull request; its URL."""
        async with self.lock(key):
            workspace, sandbox = await self.sandbox(key)
            info = workspace.info()
            if info is None:
                raise WorkspaceError("open a repository with workspace_open first")
            if info.push_url is None:
                raise WorkspaceError(f"{info.source} has no origin remote to push to")
            dirty = await sandbox.run("git status --porcelain 2>/dev/null", cwd=workspace.work, timeout_s=60)
            if dirty.output.strip():
                raise WorkspaceError("the working copy has uncommitted changes; commit them (git add -A && git commit -m ...) first")
            bundle = workspace.tmp / "publish.bundle"
            bundle.unlink(missing_ok=True)
            made = await sandbox.run(
                'git bundle create "$1" HEAD "^$2" 2>&1', args=(str(bundle), f"origin/{info.base}"), cwd=workspace.work, timeout_s=GIT_TIMEOUT_S
            )
            if made.exit_code != 0:
                if "empty bundle" in made.output:
                    raise WorkspaceError(f"there are no new commits on top of {info.base} to publish")
                raise WorkspaceError(f"packing the commits failed: {made.output[-2000:]}")
            # Host side from here on: only the bundle (data) crosses over from the sandbox.
            ref = f"refs/heads/{info.branch}"
            _ok(await _host(self.git, "-C", str(workspace.origin), "fetch", "--quiet", "--no-tags", "--", str(bundle), f"+HEAD:{ref}"), "reading the commits")
            _ok(
                await _host(
                    self.git, "-C", str(workspace.origin), "-c", "core.hooksPath=/dev/null", *self._credentials(),
                    "push", "--no-verify", "--quiet", "--", info.push_url, f"{ref}:{ref}",
                ),
                f"pushing {info.branch}",
            )
            if info.github is None or self.gh is None:
                return f"pushed {info.branch} to {info.push_url} (no GitHub repository or gh CLI to open a pull request)"
            existing = await _host(self.gh, "pr", "view", info.branch, "--repo", info.github, "--json", "url", "--jq", ".url", cwd=workspace.root)
            if existing.exit_code == 0 and existing.output.startswith("https://"):
                return existing.output
            command = [self.gh, "pr", "create", "--repo", info.github, "--head", info.branch, "--base", info.base, "--title", title, "--body", body]
            if draft:
                command.append("--draft")
            created = _ok(await _host(*command, cwd=workspace.root), "opening the pull request")
            return created.splitlines()[-1] if created else f"opened a pull request for {info.branch}"


__all__ = ["Workspace", "WorkspaceError", "WorkspaceInfo", "Workspaces"]
