"""Coding runs: Symphony's own coding agent, started, followed and resumed by Osmo.

Osmo is a host of Symphony's stdio protocol (coding_agent/protocols/stdio). A coding run is
one `symphony stdio` worker: Osmo sends the task as a `run` command and turns the worker's
frames into the run's card (phases, reasoning, result), and its approval questions into
Osmo's Approve / Deny. The agent, its tools (bash, files, patch, search, subagents), its
sessions and its rules are Symphony's; Osmo only decides how and when it runs.

How it runs:
- Inside the macOS sandbox (upstream/sandbox): it writes only its workspace and Osmo's
  Symphony home, reads the user's home only for toolchains, Symphony's keys and logins, git
  config, and the GitHub CLI's login (the user chose to let it push, behind approval).
- With Osmo's own SYMPHONY_HOME: its sessions are Osmo's, and the config there is rewritten
  from the user's before every launch with approvals on (`ask`, every bash call asks), so a
  command that edits it changes nothing for the next run.
- Every approval question comes to Osmo. A command that publishes (`git push`, `gh pr ...`)
  goes to the user; everything else runs in the sandbox, so Osmo allows it once. This is the
  agent's intent being gated, not a boundary: code the agent runs can read the GitHub login.

When it runs: a run's session id and workspace are stored with it. If Osmo stops mid-run,
the worker sees its stdin close, cancels the turn and exits with its session saved; the next
Osmo starts it again on the same session with a note to continue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from symphony_server.approvals import APPROVAL_TIMEOUT_S
from symphony_server.protocol import AgentUpdate, ApprovalRequest, ApprovalResolved
from symphony_server.storage import AgentRunRecord
from symphony_server.turn_events import activity_label
from symphony_server.upstream.sandbox import SandboxPolicy, sandbox_available, sandbox_env

if TYPE_CHECKING:
    from symphony_server.agents import AgentRuns, NoticeTag

log = logging.getLogger("symphony.coding")

ALLOW = "Allow once"
DENY = "Deny"
# Commands that act outside the machine under the user's name. Matching a command string is a
# check on what the agent means to do, not a sandbox: a script can push without saying so.
PUBLISHING = re.compile(
    r"\bgit(?:\s+-\S+(?:\s+[^-\s]\S*)?)*\s+push\b"  # `git push`, `git -C dir push`; not `git commit -m 'push'`
    r"|\bgh\s+(?:pr|repo|release|issue|api|gist|secret|variable|workflow|run|label|ssh-key|gpg-key)\b"
)
RESUME_PROMPT = (
    "[resumed] Osmo restarted while you were working, so your last step may not have finished. Check the "
    "workspace (git status, git log) before redoing anything, then finish the original task:\n\n{task}"
)
# Shared with the user's ~/.symphony, read-only in the sandbox (Symphony's skills, plugins and add-ons).
LINKED = ("skills", "plugins", "addons")
FRAME_MAX = 16 * 1024 * 1024
STOP_GRACE_S = 10.0


@dataclass
class SymphonyRunner:
    """How a `symphony stdio` worker is launched for a run."""

    executable: str
    home: Path
    workspaces: Path
    model: str | None = None
    sandbox: bool = True
    user_home: Path = field(default_factory=Path.home)

    @classmethod
    def from_settings(cls, executable: str | None, *, home: Path, workspaces: Path, model: str | None, sandbox: bool) -> SymphonyRunner | None:
        """None when coding runs are off: no `symphony`, or a sandbox was asked for where there is none."""
        if executable is None:
            return None
        if sandbox and not sandbox_available():
            log.warning("coding runs are off: the macOS sandbox is not available (OSMO_SYMPHONY_SANDBOX=0 runs Symphony unsandboxed)")
            return None
        return cls(executable=executable, home=home.expanduser(), workspaces=workspaces.expanduser(), model=model, sandbox=sandbox)

    @property
    def user_symphony(self) -> Path:
        return self.user_home / ".symphony"

    def workspace(self, run_id: str, given: str | None) -> Path | str:
        """The directory the run works in, or why `given` cannot be one."""
        if not given:
            fresh = self.workspaces / run_id
            fresh.mkdir(parents=True, exist_ok=True)
            return fresh
        path = Path(given).expanduser().resolve()
        if not path.is_dir():
            return f"{given} is not a directory"
        home = self.user_home.resolve()
        if path == Path("/") or home.is_relative_to(path):
            return f"{given} is too broad to work in; name a project directory"
        if any(path.is_relative_to(home / hidden) for hidden in (".ssh", ".symphony", "Library", ".config")):
            return f"{given} is not a project directory"
        return path

    def prepare(self) -> None:
        """Osmo's Symphony home: the user's config with approvals forced on, and their skills and plugins."""
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "tmp").mkdir(exist_ok=True)
        try:
            config: dict[str, Any] = json.loads((self.user_symphony / "config.json").read_text())
        except (OSError, ValueError):
            config = {}
        approvals = dict(config.get("approvals") or {})
        # Every bash call asks, so each publishing command reaches Osmo; the user's deny rules still apply,
        # their allow rules do not (one could allow `git push`).
        approvals.update(mode="ask", require_for_bash=True, allow=[])
        config.update(approvals=approvals, unattended=False)
        (self.home / "config.json").write_text(json.dumps(config, indent=2) + "\n")
        for name in LINKED:
            source, link = self.user_symphony / name, self.home / name
            if source.is_dir() and not link.exists():
                link.symlink_to(source)

    def policy(self, workspace: Path) -> SandboxPolicy:
        home = self.user_home
        return SandboxPolicy.confined(
            writable=(workspace, self.home, self.user_symphony / "oauth"),
            readable=(
                self.user_symphony / ".env",
                *(self.user_symphony / name for name in LINKED),
                home / ".agents",  # user-level skills
                home / ".gitconfig",
                home / ".config" / "git",
                home / ".config" / "gh",
                Path(self.executable).resolve().parent.parent,
            ),
            keychain=True,
            home=home,
        )

    def command(self, workspace: Path, session_id: str) -> list[str]:
        argv = [self.executable, "stdio", "--workspace", str(workspace), "--session-id", session_id]
        if self.model:
            argv += ["--model", self.model]
        return self.policy(workspace).wrap(argv) if self.sandbox else argv

    def environment(self) -> dict[str, str]:
        # Nothing of Osmo's own environment (its keys and settings); Symphony loads its keys itself.
        env = sandbox_env(self.user_home, self.home / "tmp", {"SYMPHONY_HOME": str(self.home)})
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
            if name in os.environ:
                env[name] = os.environ[name]
        return env


class Worker:
    """One `symphony stdio` process: JSON frames in on stdin, out on stdout."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process

    @classmethod
    async def launch(cls, argv: list[str], env: dict[str, str], cwd: Path, log_path: Path) -> Worker:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as stderr:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=stderr, start_new_session=True, limit=FRAME_MAX,
            )
        return cls(process)

    async def send(self, frame: dict[str, Any]) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            return
        stdin.write((json.dumps(frame) + "\n").encode())
        try:
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def frames(self) -> AsyncIterator[dict[str, Any]]:
        assert self.process.stdout is not None
        while line := await self.process.stdout.readline():
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if isinstance(frame, dict):
                yield frame

    async def stop(self) -> None:
        """Close stdin (the worker cancels its turn, saves the session and exits); kill it if it does not."""
        if self.process.stdin is not None and not self.process.stdin.is_closing():
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), STOP_GRACE_S)
        except asyncio.TimeoutError:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            await self.process.wait()


class CodingRuns:
    """The coding runs of one server, reported through `runs` like every agent run."""

    def __init__(self, runner: SymphonyRunner, runs: AgentRuns) -> None:
        self.runner, self.runs = runner, runs
        self.workers: dict[str, Worker] = {}
        self.cancelling: set[str] = set()
        # The runs that are coding runs (this server's, and those it resumed).
        self.ids: set[str] = set()

    async def start(self, thread_id: str, task: str, workspace: str | None) -> AgentRunRecord | str:
        run_id = str(uuid.uuid4())
        where = self.runner.workspace(run_id, workspace)
        if isinstance(where, str):
            return where
        session_id = str(uuid.uuid4())
        now = time.time()
        record = AgentRunRecord(runId=run_id, threadId=thread_id, task=task, status="running", createdAt=now, updatedAt=now)
        self.runs.runs[run_id] = record
        self.runs.sessions[run_id] = session_id
        self.runs.storage.save_run(record)
        self.runs.storage.save_coding_run(run_id, session_id, str(where))
        self.ids.add(run_id)
        await self.runs.show(thread_id, AgentUpdate(runId=run_id, task=task, status="running"))
        self.runs.spawn(self.follow(run_id, session_id, where, task))
        return record

    def recover(self) -> list[str]:
        """Start again the coding runs a stopped Osmo left running; their ids."""
        resumed = []
        for record, session_id, workspace in self.runs.storage.unfinished_coding_runs():
            record = record.model_copy(update={"status": "running", "updatedAt": time.time()})
            self.runs.runs[record.runId] = record
            self.runs.sessions[record.runId] = session_id
            self.runs.phases[record.runId] = "Resumed after Osmo restarted"
            self.runs.storage.save_run(record)
            self.ids.add(record.runId)
            self.runs.spawn(self.follow(record.runId, session_id, Path(workspace), RESUME_PROMPT.format(task=record.task)))
            resumed.append(record.runId)
        return resumed

    async def cancel(self, run_id: str) -> None:
        self.cancelling.add(run_id)
        self.runs.broker.deny_session(self.runs.sessions.get(run_id, ""))
        worker = self.workers.get(run_id)
        if worker is not None:
            await worker.send({"type": "interrupt"})
        else:
            await self.runs.update(self.runs.runs[run_id], "cancelled")

    # ------------------------------------------------------------ one worker

    async def follow(self, run_id: str, session_id: str, workspace: Path, prompt: str) -> None:
        result, error, status = "", "", ""
        try:
            self.runner.prepare()
            worker = await Worker.launch(
                self.runner.command(workspace, session_id), self.runner.environment(), workspace,
                self.runner.home / "logs" / f"{session_id}.log",
            )
        except (OSError, RuntimeError) as exc:
            await self.finish(run_id, "failed", f"Could not start Symphony: {exc}")
            return
        self.workers[run_id] = worker
        try:
            async for frame in worker.frames():
                kind = frame.get("type")
                if kind == "ready":
                    await worker.send({"type": "run", "prompt": prompt})
                elif kind == "event":
                    result = await self.on_event(run_id, str(frame.get("event")), _object(frame.get("payload"))) or result
                elif kind == "input_requested":
                    self.runs.spawn(self.answer(run_id, session_id, worker, frame))
                elif kind == "error":
                    error = str(frame.get("message") or "")
                elif kind == "done":
                    status = str(frame.get("status") or "")
                    break
        finally:
            # Also on cancellation (Osmo shutting down): the worker saves its session, the run stays `running`.
            self.workers.pop(run_id, None)
            await worker.stop()
        if status == "completed":
            await self.finish(run_id, "completed", result or "Done.")
        elif status == "interrupted" and run_id in self.cancelling:
            await self.finish(run_id, "cancelled")
        else:
            await self.finish(run_id, "failed", error or "Symphony stopped before finishing.")

    async def on_event(self, run_id: str, name: str, payload: dict[str, Any]) -> str | None:
        """Show what the worker does; the run's answer when it is done."""
        record = self.runs.runs[run_id]
        if name == "tool_execution_started":
            await self.runs.update(record, "running", phase=activity_label(str(payload.get("tool_name") or ""), _object(payload.get("arguments"))))
        elif name == "reasoning_delta" and payload.get("delta") and not payload.get("parent_call_id"):
            await self.runs.show(record.threadId, AgentUpdate(runId=run_id, task=record.task, status=record.status, reasoning=str(payload["delta"])))
        elif name == "run_completed" and not payload.get("parent_call_id"):
            return str(payload.get("output_text") or "")
        return None

    async def answer(self, run_id: str, session_id: str, worker: Worker, frame: dict[str, Any]) -> None:
        request_id = str(frame.get("request_id") or "")
        metadata = _object(frame.get("metadata"))
        if frame.get("kind") == "approval":
            tool = str(metadata.get("tool_name") or "")
            target = str(frame.get("target") or metadata.get("target") or "")
            publishing = tool == "bash" and PUBLISHING.search(target) is not None
            value = (ALLOW if await self.ask(run_id, session_id, tool, target) else DENY) if publishing else ALLOW
        else:
            # ask_user: nobody is at a keyboard; Symphony's unattended runs answer the same way.
            value = str(frame.get("default") or "Use your best judgment and continue.")
        await worker.send({"type": "answer", "request_id": request_id, "value": value})

    async def ask(self, run_id: str, session_id: str, tool: str, command: str) -> bool:
        """The user's Approve / Deny on a publishing command."""
        runs = self.runs
        record = runs.runs[run_id]
        approval_id = runs.broker.open(session_id)
        summary = f"Publish · {' '.join(command.split())[:120]}"
        request = ApprovalRequest(approvalId=approval_id, runId=run_id, tool=tool, summary=summary, arguments=command)
        await runs.update(record, "waiting_approval", phase=summary)
        runs.asking[run_id] = request
        await runs.show(record.threadId, request)
        await runs.notify(record, runs.notice(run_id, "interrupt", f"[agent {run_id}] needs approval to: {summary}. Ask the user to tap Approve or Deny on screen."))
        approved = await runs.broker.wait(approval_id, APPROVAL_TIMEOUT_S)
        record = runs.runs[run_id]
        await runs.update(record, "running")
        await runs.show(record.threadId, ApprovalResolved(approvalId=approval_id, runId=run_id, approved=approved))
        await runs.notify(record, runs.notice(run_id, "silent", f"[agent {run_id}] the user {'approved' if approved else 'denied'} the step"))
        return approved

    async def finish(self, run_id: str, status: str, result: str | None = None) -> None:
        self.cancelling.discard(run_id)
        record = self.runs.runs[run_id]
        await self.runs.update(record, status, result=result)  # type: ignore[arg-type]
        text = {"completed": f"done: {result}", "failed": f"failed: {result}", "cancelled": "was cancelled"}[status]
        tag: NoticeTag = "silent" if status == "cancelled" else "interrupt"
        await self.runs.notify(record, self.runs.notice(run_id, tag, f"[agent {run_id}] {text}"[:4000]))


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["CodingRuns", "PUBLISHING", "SymphonyRunner", "Worker"]
