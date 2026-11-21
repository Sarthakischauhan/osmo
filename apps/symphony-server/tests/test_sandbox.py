"""The macOS sandbox and the coding workspace tools: confinement, and a commit-to-pull-request flow."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from core_harness import EventSink

from symphony_server.turn_events import activity_label
from symphony_server.upstream.sandbox import (
    RUN_WORKSPACE,
    SandboxPolicy,
    SeatbeltSandbox,
    WorkspaceError,
    Workspaces,
    sandbox_available,
    sandbox_env,
    workspace_tools,
)

pytestmark = pytest.mark.skipif(not sandbox_available(), reason="needs macOS sandbox-exec")


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY")
    (home / "notes").mkdir()
    (home / "notes" / "secret.txt").write_text("diary")
    (home / ".cargo" / "bin").mkdir(parents=True)
    (home / ".cargo" / "bin" / "tool").write_text("toolchain")
    return home


async def test_the_sandbox_writes_only_its_workspace_and_cannot_read_home(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSMO_TEST_SECRET", "sk-live-123")
    work, scratch = tmp_path / "work", tmp_path / "scratch"
    work.mkdir()
    scratch.mkdir()
    sandbox = SeatbeltSandbox(SandboxPolicy.for_workspace(work, (scratch,), home=home), sandbox_env(scratch, scratch))

    async def run(command: str) -> str:
        return (await sandbox.run(command, cwd=work, timeout_s=30)).text()

    assert "[exit code 0]" in await run("echo hi > made.txt && cat made.txt")
    assert (work / "made.txt").read_text() == "hi\n"
    assert "Operation not permitted" in await run(f"echo x > {home}/planted.txt")
    assert not (home / "planted.txt").exists()
    assert "Operation not permitted" in await run(f"cat {home}/notes/secret.txt")
    assert "Operation not permitted" in await run(f"cat {home}/.ssh/id_ed25519")
    assert "toolchain" in await run(f"cat {home}/.cargo/bin/tool")
    assert "sk-live-123" not in await run("env")
    assert "Operation not permitted" in await run("osascript -e 'return 1'")
    assert "Operation not permitted" in await run("open -a TextEdit")


async def test_a_command_is_stopped_at_its_timeout(tmp_path: Path) -> None:
    sandbox = SeatbeltSandbox(SandboxPolicy.for_workspace(tmp_path), sandbox_env(tmp_path, tmp_path))
    result = await sandbox.run("echo started; sleep 30", cwd=tmp_path, timeout_s=0.5)
    assert result.timed_out and "started" in result.output


async def test_without_network_nothing_connects(tmp_path: Path) -> None:
    sandbox = SeatbeltSandbox(SandboxPolicy.for_workspace(tmp_path, network=False), sandbox_env(tmp_path, tmp_path))
    result = await sandbox.run("python3 -c 'import socket; socket.create_connection((\"1.1.1.1\", 80), 3)'", cwd=tmp_path, timeout_s=10)
    assert result.exit_code != 0 and "Operation not permitted" in result.output


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """A local checkout whose origin is a bare repository standing in for GitHub."""
    remote, source = tmp_path / "remote.git", tmp_path / "source"
    git("init", "--quiet", "--bare", "-b", "main", str(remote))
    git("init", "--quiet", "-b", "main", str(source))
    (source / "app.py").write_text("def greet():\n    return 'helo'\n")
    git("add", "-A", cwd=source)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first", cwd=source)
    git("remote", "add", "origin", str(remote), cwd=source)
    git("push", "--quiet", "origin", "main", cwd=source)
    return source, remote


async def call(tools: dict[str, object], name: str, **args: object) -> dict[str, object] | str:
    raw = await tools[name].execute(sink=EventSink(), args=args)  # type: ignore[attr-defined]
    try:
        parsed: dict[str, object] = json.loads(raw)
        return parsed
    except json.JSONDecodeError:
        return str(raw)


async def test_an_agent_fixes_code_commits_and_publishes_a_branch(tmp_path: Path, repo: tuple[Path, Path]) -> None:
    source, remote = repo
    tools = {tool.name: tool for tool in workspace_tools(Workspaces(tmp_path / "workspaces", gh=None))}
    RUN_WORKSPACE.set("agent-1")

    opened = await call(tools, "workspace_open", repo=str(source), branch="osmo/fix-typo")
    assert opened == {"repo": str(source), "branch": "osmo/fix-typo", "base": "main", "can_open_pull_request": False}
    # Opening again (as a resumed run does) returns the same workspace.
    assert await call(tools, "workspace_open", repo=str(source)) == opened

    assert "return 'helo'" in str(await call(tools, "read_file", path="app.py"))
    assert await call(tools, "edit_file", path="app.py", old="'helo'", new="'hello'") == {"edited": "app.py"}
    assert await call(tools, "write_file", path="tests/test_app.py", content="from app import greet\n") == {"written": "tests/test_app.py", "bytes": 22}
    assert await call(tools, "read_file", path="../outside.txt") == {"error": "paths are relative to the workspace root and stay inside it"}
    assert "uncommitted changes" in str(await call(tools, "open_pull_request", title="Fix typo"))

    committed = await call(tools, "shell", command="git add -A && git commit -q -m 'Fix greeting typo' && git log --format=%an -1")
    assert "[exit code 0]" in str(committed)

    # A sandboxed process tries to hijack the host's push: a hook, an fsmonitor, a redirected remote.
    work = tmp_path / "workspaces" / "agent-1" / "work"
    marker = tmp_path / "pwned"
    hijack = (
        f"printf '#!/bin/sh\\ntouch {marker}\\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push && "
        f"git config core.fsmonitor 'touch {marker} #' && git config url.{tmp_path}/evil.git.insteadOf {remote}"
    )
    assert "[exit code 0]" in str(await call(tools, "shell", command=hijack))

    published = await call(tools, "open_pull_request", title="Fix greeting typo", body="Fixes the typo.")
    assert published == {"pull_request": f"pushed osmo/fix-typo to {remote} (no GitHub repository or gh CLI to open a pull request)"}
    assert not marker.exists()
    assert git("--git-dir", str(remote), "log", "--format=%s", "-1", "osmo/fix-typo") == "Fix greeting typo"
    assert "hello" in git("--git-dir", str(remote), "show", "osmo/fix-typo:app.py")
    # The user's own checkout was never touched.
    assert git("branch", "--show-current", cwd=source) == "main" and "helo" in (source / "app.py").read_text()
    assert work.is_dir()

    # Publishing again after more work pushes the new commit to the same branch.
    await call(tools, "shell", command="echo '# more' >> app.py && git commit -qam 'More'")
    await call(tools, "open_pull_request", title="Fix greeting typo")
    assert git("--git-dir", str(remote), "log", "--format=%s", "-1", "osmo/fix-typo") == "More"


async def test_publishing_nothing_new_is_refused(tmp_path: Path, repo: tuple[Path, Path]) -> None:
    workspaces = Workspaces(tmp_path / "workspaces", gh=None)
    await workspaces.open("k", str(repo[0]))
    with pytest.raises(WorkspaceError, match="no new commits"):
        await workspaces.publish("k", "Nothing", "")


@pytest.mark.parametrize("source", ["ext::sh -c touch% /tmp/x", "file:///etc", "git@github.com:a/b.git", "/no/such/repo"])
async def test_only_github_https_and_local_repositories_open(tmp_path: Path, source: str) -> None:
    with pytest.raises(WorkspaceError, match="repo must be"):
        await Workspaces(tmp_path, gh=None).open("k", source)


async def test_workspace_tools_need_a_run() -> None:
    [opener] = [tool for tool in workspace_tools(Workspaces(Path("/nonexistent"), gh=None)) if tool.name == "workspace_open"]
    token = RUN_WORKSPACE.set(None)
    try:
        assert "error" in json.loads(await opener.execute(sink=EventSink(), args={"repo": "a/b"}))
    finally:
        RUN_WORKSPACE.reset(token)


def test_the_approval_card_says_what_will_be_published() -> None:
    assert activity_label("open_pull_request", {"title": "Fix typo"}) == "Opening a pull request · Fix typo"
    assert activity_label("shell", {"command": "npm   test"}) == "Running · npm test"
