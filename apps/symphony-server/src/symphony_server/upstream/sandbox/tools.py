"""Harness tools for coding work in a sandboxed per-run workspace.

``workspace_tools(workspaces)`` returns the tools; add ``workspace_link`` to the
server's addon factories so each run's tools find its workspace (by session id, so a
resumed run is back in the same directory).

Everything the model runs (``shell``) and every file it reads or writes goes through
the sandbox, never through host file APIs, so a symlink planted by a command cannot
point a host-side read or write outside the workspace. Only ``workspace_open`` (a
clone) and ``open_pull_request`` (a push) run on the host. ``open_pull_request`` is
the one tool that acts outside the machine; gate it on approval.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Optional

from core_harness import Addon, EventSink, Tool
from core_server.models import RunContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .seatbelt import CommandResult
from .workspace import WorkspaceError, Workspaces

RUN_WORKSPACE: ContextVar[Optional[str]] = ContextVar("symphony_workspace", default=None)

SHELL_TIMEOUT_S = 120.0
SHELL_TIMEOUT_MAX_S = 900.0
READ_MAX = 60_000
WRITE_MAX = 1_000_000

PUBLISH_TOOL = "open_pull_request"
WORKSPACE_TOOL_NAMES = frozenset({"workspace_open", "shell", "read_file", "write_file", "edit_file", PUBLISH_TOOL})


class WorkspaceLink(Addon):
    """Points this run's workspace tools at its workspace (keyed by session id)."""

    name = "workspace_link"

    def __init__(self, key: str) -> None:
        self.key = key

    async def before_run(self, **payload: Any) -> None:
        RUN_WORKSPACE.set(self.key)


def workspace_link(context: RunContext) -> WorkspaceLink:
    return WorkspaceLink(context.session_id)


class OpenArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(min_length=1, description="owner/name on GitHub, an https:// git URL, or a local repository path.")
    branch: Optional[str] = Field(default=None, description="Branch to create for the work; one is generated when omitted.")


class ShellArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, description="A bash command, run in the workspace root.")
    timeout_s: float = Field(default=SHELL_TIMEOUT_S, gt=0, le=SHELL_TIMEOUT_MAX_S)


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, description="Path relative to the workspace root.")
    offset: int = Field(default=1, ge=1, description="First line to return (1-based).")
    limit: int = Field(default=400, ge=1, le=4000, description="How many lines.")


class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    content: str = Field(max_length=WRITE_MAX)


class EditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    old: str = Field(min_length=1, description="Exact text to replace; must occur exactly once.")
    new: str


class PublishArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=60_000)
    draft: bool = False


def _relative(path: str) -> str:
    # The sandbox is what confines file access (it writes only the workspace and cannot read the
    # user's home); this only keeps the model's paths relative and tidy.
    cleaned = path.strip()
    if cleaned.startswith(("/", "~")) or ".." in cleaned.split("/"):
        raise WorkspaceError("paths are relative to the workspace root and stay inside it")
    return cleaned


class WorkspaceTool(Tool):
    args_model: type[BaseModel]

    def __init__(self, workspaces: Workspaces, *, name: str, description: str, args_model: type[BaseModel]) -> None:
        super().__init__(name=name, description=description, parameters=args_model.model_json_schema())
        self.workspaces = workspaces
        self.args_model = args_model

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        del sink
        key = RUN_WORKSPACE.get()
        if key is None:
            return json.dumps({"error": f"{self.name} runs inside a server run with a workspace"})
        try:
            return await self.act(key, self.args_model.model_validate(args))
        except (ValidationError, WorkspaceError) as exc:
            return json.dumps({"error": str(exc)})

    async def act(self, key: str, args: Any) -> str:
        raise NotImplementedError


class WorkspaceOpenTool(WorkspaceTool):
    def __init__(self, workspaces: Workspaces) -> None:
        super().__init__(
            workspaces,
            name="workspace_open",
            args_model=OpenArgs,
            description=(
                "Clone a repository into this run's sandboxed workspace and check out a new branch. "
                "Call it again after a restart: it returns the existing workspace with your work in it."
            ),
        )

    async def act(self, key: str, args: OpenArgs) -> str:
        info = await self.workspaces.open(key, args.repo, args.branch)
        return json.dumps({"repo": info.source, "branch": info.branch, "base": info.base, "can_open_pull_request": info.github is not None})


class ShellTool(WorkspaceTool):
    def __init__(self, workspaces: Workspaces) -> None:
        super().__init__(
            workspaces,
            name="shell",
            args_model=ShellArgs,
            description=(
                "Run a bash command in this run's workspace, inside a macOS sandbox: it can read and write only the "
                "workspace, has network access but no credentials (no SSH keys, no GitHub login, no keychain). Use it "
                "to explore (rg, git log), build, test, and commit (git add -A && git commit -m ...). It cannot push."
            ),
        )

    async def act(self, key: str, args: ShellArgs) -> str:
        workspace, sandbox = await self.workspaces.sandbox(key)
        return (await sandbox.run(args.command, cwd=workspace.work, timeout_s=args.timeout_s)).text()


class ReadFileTool(WorkspaceTool):
    def __init__(self, workspaces: Workspaces) -> None:
        super().__init__(workspaces, name="read_file", args_model=ReadArgs, description="Read lines of a file in the workspace, numbered.")

    async def act(self, key: str, args: ReadArgs) -> str:
        workspace, sandbox = await self.workspaces.sandbox(key)
        last = args.offset + args.limit - 1
        result = await sandbox.run('awk -v a="$2" -v b="$3" \'NR>=a && NR<=b {printf "%6d  %s\\n", NR, $0}\' "./$1"', args=(_relative(args.path), str(args.offset), str(last)), cwd=workspace.work, output_max=READ_MAX)
        return _checked(result, args.path)


class WriteFileTool(WorkspaceTool):
    def __init__(self, workspaces: Workspaces) -> None:
        super().__init__(workspaces, name="write_file", args_model=WriteArgs, description="Create or overwrite a file in the workspace.")

    async def act(self, key: str, args: WriteArgs) -> str:
        workspace, sandbox = await self.workspaces.sandbox(key)
        result = await sandbox.run('mkdir -p "$(dirname "./$1")" && cat > "./$1"', args=(_relative(args.path),), stdin=args.content.encode(), cwd=workspace.work)
        _checked(result, args.path)
        return json.dumps({"written": args.path, "bytes": len(args.content.encode())})


class EditFileTool(WorkspaceTool):
    def __init__(self, workspaces: Workspaces) -> None:
        super().__init__(workspaces, name="edit_file", args_model=EditArgs, description="Replace one exact occurrence of `old` with `new` in a workspace file.")

    async def act(self, key: str, args: EditArgs) -> str:
        workspace, sandbox = await self.workspaces.sandbox(key)
        path = _relative(args.path)
        current = await sandbox.run('cat "./$1"', args=(path,), cwd=workspace.work, output_max=WRITE_MAX + 1)
        text = _checked(current, args.path)
        if current.truncated:
            raise WorkspaceError(f"{args.path} is too large to edit this way; use shell")
        count = text.count(args.old)
        if count != 1:
            raise WorkspaceError(f"`old` occurs {count} times in {args.path}; it must occur exactly once")
        result = await sandbox.run('cat > "./$1"', args=(path,), stdin=text.replace(args.old, args.new).encode(), cwd=workspace.work)
        _checked(result, args.path)
        return json.dumps({"edited": args.path})


class OpenPullRequestTool(WorkspaceTool):
    def __init__(self, workspaces: Workspaces) -> None:
        super().__init__(
            workspaces,
            name=PUBLISH_TOOL,
            args_model=PublishArgs,
            description=(
                "Push the workspace branch's commits and open a pull request (or return the one already open). Commit "
                "first. Needs the user's approval; write a clear title and a body saying what changed and how it was tested."
            ),
        )

    async def act(self, key: str, args: PublishArgs) -> str:
        url = await self.workspaces.publish(key, args.title, args.body, draft=args.draft)
        return json.dumps({"pull_request": url})


def _checked(result: CommandResult, path: str) -> str:
    if result.exit_code != 0:
        raise WorkspaceError(f"{path}: {result.output.strip()[-500:] or 'failed'}")
    return result.output


def workspace_tools(workspaces: Workspaces) -> list[Tool]:
    return [
        WorkspaceOpenTool(workspaces),
        ShellTool(workspaces),
        ReadFileTool(workspaces),
        WriteFileTool(workspaces),
        EditFileTool(workspaces),
        OpenPullRequestTool(workspaces),
    ]


__all__ = ["PUBLISH_TOOL", "RUN_WORKSPACE", "WORKSPACE_TOOL_NAMES", "WorkspaceLink", "workspace_link", "workspace_tools"]
