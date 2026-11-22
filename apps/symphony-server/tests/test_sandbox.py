"""The macOS sandbox (Seatbelt) that coding runs execute in."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from symphony_server.upstream.sandbox import SandboxPolicy, SeatbeltSandbox, sandbox_available, sandbox_env

pytestmark = pytest.mark.skipif(not sandbox_available(), reason="needs macOS sandbox-exec")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY")
    (home / "notes").mkdir()
    (home / "notes" / "secret.txt").write_text("diary")
    (home / ".cargo").mkdir()
    (home / ".cargo" / "tool").write_text("toolchain")
    (home / ".gitconfig").write_text("[user]\n\tname = me\n")
    return home


def sandbox(home: Path, work: Path, **options: bool) -> SeatbeltSandbox:
    work.mkdir(exist_ok=True)
    policy = SandboxPolicy.confined((work,), (home / ".gitconfig",), home=home, **options)
    return SeatbeltSandbox(policy, sandbox_env(home, work))


async def test_writes_stay_in_the_workspace_and_home_stays_unread(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSMO_TEST_SECRET", "sk-live-123")
    work = tmp_path / "work"
    box = sandbox(home, work)

    async def run(command: str) -> str:
        return (await box.run(command, cwd=work, timeout_s=30)).text()

    assert "[exit code 0]" in await run("echo hi > made.txt")
    assert (work / "made.txt").read_text() == "hi\n"
    assert "Operation not permitted" in await run(f"echo x > {home}/planted.txt")
    assert "Operation not permitted" in await run(f"cat {home}/notes/secret.txt")
    assert "Operation not permitted" in await run(f"cat {home}/.ssh/id_ed25519")
    assert "toolchain" in await run(f"cat {home}/.cargo/tool")
    assert "name = me" in await run(f"cat {home}/.gitconfig")  # granted
    assert "sk-live-123" not in await run("env")
    assert "Operation not permitted" in await run("osascript -e 'return 1'")
    assert "Operation not permitted" in await run("open -a TextEdit")
    assert "Operation not permitted" in await run("security find-generic-password -s none")


async def test_the_keychain_opens_only_when_granted(tmp_path: Path, home: Path) -> None:
    granted = sandbox(home, tmp_path / "work", keychain=True)
    result = await granted.run("security find-generic-password -s osmo-test-does-not-exist", cwd=tmp_path / "work", timeout_s=30)
    assert "Operation not permitted" not in result.output


async def test_a_command_is_stopped_at_its_timeout(tmp_path: Path, home: Path) -> None:
    result = await sandbox(home, tmp_path / "work").run("echo started; sleep 30", cwd=tmp_path / "work", timeout_s=0.5)
    assert result.timed_out and "started" in result.output


async def test_without_network_nothing_connects(tmp_path: Path, home: Path) -> None:
    box = sandbox(home, tmp_path / "work", network=False)
    result = await box.run("/usr/bin/python3 -c 'import socket; socket.create_connection((\"1.1.1.1\", 80), 3)'", cwd=tmp_path / "work", timeout_s=10)
    assert result.exit_code != 0 and "Operation not permitted" in result.output


def test_wrap_prefixes_sandbox_exec(tmp_path: Path, home: Path) -> None:
    argv = SandboxPolicy.confined((tmp_path,), home=home).wrap(["symphony", "stdio"])
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"] and argv[-2:] == ["symphony", "stdio"]
    assert asyncio.iscoroutinefunction(SeatbeltSandbox.run)
