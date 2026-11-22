"""Coding runs: Symphony's coding agent over its stdio protocol, gated, sandboxed and resumed by Osmo."""

from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import make_client, xai_key_store
from symphony_server.settings import Settings
from symphony_server.symphony_runs import PUBLISHING, SymphonyRunner
from symphony_server.upstream.sandbox import sandbox_available

# A stand-in for `symphony stdio`: the protocol's frames, one scripted turn, a log of what it was sent.
FAKE_SYMPHONY = r'''#!{python}
import json, os, sys
home = os.environ["SYMPHONY_HOME"]
args = sys.argv[1:]
session = args[args.index("--session-id") + 1]
def out(frame):
    sys.stdout.write(json.dumps(frame) + "\n"); sys.stdout.flush()
def log(entry):
    with open(os.path.join(home, "fake.log"), "a") as f:
        f.write(json.dumps(entry) + "\n")
def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None
def ask(request_id, command):
    out({{"type": "event", "event": "question_asked", "payload": {{"request_id": request_id}}}})
    out({{"type": "input_requested", "request_id": request_id, "kind": "approval", "question": "Allow bash command once?",
         "choices": ["Allow once", "Always allow", "Deny"], "default": "Allow once", "target": command,
         "metadata": {{"tool_name": "bash", "target": command}}}})
    while True:
        frame = read()
        if frame is None:
            sys.exit(0)
        if frame.get("type") == "answer" and frame.get("request_id") == request_id:
            return frame["value"]
out({{"type": "ready", "session_id": session, "protocol_version": 4}})
while True:
    frame = read()
    if frame is None:
        sys.exit(0)
    if frame.get("type") != "run":
        continue
    log({{"session": session, "prompt": frame["prompt"], "cwd": os.getcwd(),
         "approvals": json.load(open(os.path.join(home, "config.json")))["approvals"]}})
    out({{"type": "event", "event": "tool_execution_started", "payload": {{"tool_name": "bash", "arguments": {{"command": "pytest -q"}}}}}})
    if os.path.exists(os.path.join(home, "hang")):
        read()  # until Osmo stops: stdin closes
        out({{"type": "done", "status": "interrupted"}}); sys.exit(0)
    tests = ask("q-1", "npm test")
    push = ask("q-2", "git push -u origin osmo/fix")
    out({{"type": "event", "event": "run_completed", "payload": {{"output_text": f"tests={{tests}} push={{push}}"}}}})
    out({{"type": "done", "status": "completed"}})
'''


@pytest.fixture
def fake_symphony(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "symphony"
    path.parent.mkdir()
    # A system interpreter: the sandbox cannot read this checkout's virtualenv (it is under $HOME).
    path.write_text(FAKE_SYMPHONY.format(python="/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def server(tmp_path: Path, symphony: Path, *, sandbox: bool = False) -> Any:
    settings = Settings(
        xai_base_url="http://127.0.0.1:9/v1", database_file=tmp_path / "osmo.db", tool_categories=("search",),
        symphony_path=str(symphony), symphony_home=tmp_path / "symphony-home", workspaces_dir=tmp_path / "workspaces",
        symphony_sandbox=sandbox,
    )
    return make_client(settings, secret_store=xai_key_store())


def wait_for(client: Any, run_id: str, predicate: Any, timeout: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listed = client.get("/api/runs").json()
        run = next((item for item in listed["runs"] if item["runId"] == run_id), None)
        if run is not None and predicate(run, listed):
            return listed
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never got there: {client.get('/api/runs').json()}")


def start_code(client: Any, task: str) -> str:
    thread = client.post("/api/threads", json={}).json()
    response = client.post("/api/runs/start/task", json={"threadId": thread["id"], "task": task, "code": True})
    assert response.status_code == 201, response.text
    run_id: str = response.json()["runId"]
    return run_id


def entries(tmp_path: Path) -> list[dict[str, Any]]:
    log = tmp_path / "symphony-home" / "fake.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("sandbox", [False, pytest.param(True, marks=pytest.mark.skipif(not sandbox_available(), reason="needs macOS"))])
def test_a_coding_run_asks_the_user_only_before_it_publishes(tmp_path: Path, fake_symphony: Path, sandbox: bool) -> None:
    with server(tmp_path, fake_symphony, sandbox=sandbox) as client:
        run_id = start_code(client, "Fix the flaky test and open a PR")
        # `npm test` was allowed by Osmo (it runs in the sandbox); `git push` waits for the user.
        listed = wait_for(client, run_id, lambda run, listed: bool(listed["approvals"]))
        [asked] = listed["approvals"]
        assert asked["arguments"] == "git push -u origin osmo/fix" and asked["summary"].startswith("Publish · git push")
        assert client.post(f"/api/approvals/{asked['approvalId']}", json={"approved": True}).status_code == 204
        done = wait_for(client, run_id, lambda run, _: run["status"] == "completed")
        [run] = [item for item in done["runs"] if item["runId"] == run_id]
        assert run["result"] == "tests=Allow once push=Allow once"
    [entry] = entries(tmp_path)
    assert entry["prompt"] == "Fix the flaky test and open a PR"
    assert Path(entry["cwd"]).parent == (tmp_path / "workspaces").resolve()  # a fresh workspace per run
    assert entry["approvals"]["mode"] == "ask" and entry["approvals"]["require_for_bash"] is True


def test_a_denied_push_is_answered_deny(tmp_path: Path, fake_symphony: Path) -> None:
    with server(tmp_path, fake_symphony) as client:
        run_id = start_code(client, "Open a PR")
        [asked] = wait_for(client, run_id, lambda run, listed: bool(listed["approvals"]))["approvals"]
        client.post(f"/api/approvals/{asked['approvalId']}", json={"approved": False})
        done = wait_for(client, run_id, lambda run, _: run["status"] == "completed")
        assert [item["result"] for item in done["runs"]] == ["tests=Allow once push=Deny"]


def test_a_coding_run_survives_an_osmo_restart_on_the_same_session(tmp_path: Path, fake_symphony: Path) -> None:
    (tmp_path / "symphony-home").mkdir()
    (tmp_path / "symphony-home" / "hang").touch()
    with server(tmp_path, fake_symphony) as first:
        run_id = start_code(first, "Refactor the parser")
        wait_for(first, run_id, lambda run, _: bool(entries(tmp_path)))
    # Osmo stopped mid-run: the run is still `running`, and the worker exited when its stdin closed.
    (tmp_path / "symphony-home" / "hang").unlink()
    with server(tmp_path, fake_symphony) as second:
        [asked] = wait_for(second, run_id, lambda run, listed: bool(listed["approvals"]))["approvals"]
        second.post(f"/api/approvals/{asked['approvalId']}", json={"approved": True})
        wait_for(second, run_id, lambda run, _: run["status"] == "completed")
    first_turn, resumed = entries(tmp_path)
    assert resumed["session"] == first_turn["session"] and resumed["cwd"] == first_turn["cwd"]
    assert resumed["prompt"].startswith("[resumed]") and "Refactor the parser" in resumed["prompt"]


def test_coding_runs_are_refused_without_symphony(tmp_path: Path) -> None:
    settings = Settings(xai_base_url="http://127.0.0.1:9/v1", tool_categories=("search",))
    with make_client(settings, secret_store=xai_key_store()) as client:
        thread = client.post("/api/threads", json={}).json()
        response = client.post("/api/runs/start/task", json={"threadId": thread["id"], "task": "fix it", "code": True})
        assert response.status_code == 400 and "symphony" in response.json()["detail"]


def test_osmo_rewrites_its_symphony_config_with_approvals_on(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    (user_home / ".symphony" / "skills").mkdir(parents=True)
    config = {"model": "openai:gpt-x", "approvals": {"mode": "always_allow", "require_for_bash": False, "allow": ["git push*"], "deny": ["rm -rf /*"]}}
    (user_home / ".symphony" / "config.json").write_text(json.dumps(config))
    runner = SymphonyRunner("/bin/echo", home=tmp_path / "osmo-symphony", workspaces=tmp_path / "ws", user_home=user_home)
    runner.prepare()
    written = json.loads((tmp_path / "osmo-symphony" / "config.json").read_text())
    assert written["model"] == "openai:gpt-x"
    assert written["approvals"] == {"mode": "ask", "require_for_bash": True, "allow": [], "deny": ["rm -rf /*"]}
    assert (tmp_path / "osmo-symphony" / "skills").resolve() == (user_home / ".symphony" / "skills").resolve()


def test_workspaces_must_be_project_directories(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    (user_home / ".ssh").mkdir(parents=True)
    (user_home / "projects" / "app").mkdir(parents=True)
    runner = SymphonyRunner("/bin/echo", home=tmp_path / "h", workspaces=tmp_path / "ws", user_home=user_home)
    assert runner.workspace("r1", str(user_home / "projects" / "app")) == (user_home / "projects" / "app").resolve()
    assert runner.workspace("r2", None) == tmp_path / "ws" / "r2"
    assert "too broad" in str(runner.workspace("r3", str(user_home)))
    assert "not a project" in str(runner.workspace("r4", str(user_home / ".ssh")))
    assert "not a directory" in str(runner.workspace("r5", str(tmp_path / "missing")))


@pytest.mark.parametrize(
    ("command", "publishes"),
    [
        ("git push -u origin osmo/fix", True),
        ("cd app && git -C . push --force", True),
        ("gh pr create --fill", True),
        ("gh api repos/a/b/pulls -f title=x", True),
        ("git status && git commit -m 'push button'", False),
        ("gh pr", True),
        ("npm test", False),
        ("ls ghost", False),
    ],
)
def test_publishing_commands_are_recognized(command: str, publishes: bool) -> None:
    assert (PUBLISHING.search(command) is not None) == publishes
