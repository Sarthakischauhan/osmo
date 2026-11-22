"""SQLite storage and the thread REST API the sidebar and home screen use."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import make_client
from symphony_server.settings import Settings
from symphony_server.storage import DEFAULT_THREAD_TITLE, AgentRunRecord, SQLiteStorage


def test_threads_messages_and_runs_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "osmo.db"
    storage = SQLiteStorage(path)
    thread = storage.create_thread()
    storage.add_message(thread.id, "user", "hello")
    storage.save_run(AgentRunRecord(runId="r1", threadId=thread.id, task="t", status="running", createdAt=1, updatedAt=1))
    storage.save_connection("xai", "api_key")
    storage.close()

    reopened = SQLiteStorage(path)
    stored = reopened.get_thread(thread.id)
    assert stored is not None and (stored.id, stored.title) == (thread.id, DEFAULT_THREAD_TITLE)
    assert [(m.role, m.text) for m in reopened.messages(thread.id)] == [("user", "hello")]
    assert reopened.get_run("r1") is not None
    assert [(c.provider, c.kind) for c in reopened.connections()] == [("xai", "api_key")]


def test_restart_marks_unfinished_runs_interrupted(tmp_path: Path) -> None:
    path = tmp_path / "osmo.db"
    storage = SQLiteStorage(path)
    thread = storage.create_thread()
    storage.save_run(AgentRunRecord(runId="live", threadId=thread.id, task="research", status="running", createdAt=1, updatedAt=1))
    storage.save_run(AgentRunRecord(runId="asking", threadId=thread.id, task="click", status="waiting_approval", createdAt=2, updatedAt=2))
    storage.save_run(AgentRunRecord(runId="done", threadId=thread.id, task="done", status="completed", result="ok", createdAt=3, updatedAt=3))
    storage.close()

    reopened = SQLiteStorage(path)
    assert reopened.interrupt_unfinished("Interrupted when Osmo restarted.") == 2
    live, asking, done = reopened.get_run("live"), reopened.get_run("asking"), reopened.get_run("done")
    assert live is not None and asking is not None and done is not None
    assert (live.status, asking.status, done.status) == ("failed", "failed", "completed")
    assert reopened.interrupt_unfinished("again") == 0


def test_fork_copies_messages_through_one_and_delete_cascades() -> None:
    storage = SQLiteStorage(None)
    thread = storage.create_thread("Trip")
    first = storage.add_message(thread.id, "user", "plan a trip")
    assert first is not None
    storage.add_message(thread.id, "assistant", "where to?")
    storage.save_run(AgentRunRecord(runId="r1", threadId=thread.id, task="t", status="completed", createdAt=1, updatedAt=1))

    fork = storage.fork_thread(thread.id, first.id)
    assert fork is not None and fork.forkedFrom == thread.id and fork.title == "Trip (fork)"
    assert [m.text for m in storage.messages(fork.id)] == ["plan a trip"]

    assert storage.delete_thread(thread.id)
    assert storage.messages(thread.id) == [] and storage.runs(thread.id) == [] and storage.get_run("r1") is None
    assert storage.add_message(thread.id, "user", "late") is None
    assert [m.text for m in storage.messages(fork.id)] == ["plan a trip"]


def threads_client() -> TestClient:
    return make_client(Settings(xai_base_url="http://127.0.0.1:9/v1"))


def test_thread_api_lists_creates_renames_forks_and_deletes() -> None:
    with threads_client() as client:
        created = client.post("/api/threads", json={})
        assert created.status_code == 201 and created.json()["title"] == DEFAULT_THREAD_TITLE
        thread_id = created.json()["id"]

        renamed = client.patch(f"/api/threads/{thread_id}", json={"title": "Dinner plans"})
        assert renamed.json()["title"] == "Dinner plans"
        assert client.patch(f"/api/threads/{thread_id}", json={"title": "   "}).status_code == 422

        fork = client.post(f"/api/threads/{thread_id}/fork", json={})
        assert fork.status_code == 201 and fork.json()["forkedFrom"] == thread_id

        listed = [t["id"] for t in client.get("/api/threads").json()["threads"]]
        assert set(listed) == {thread_id, fork.json()["id"]}
        detail = client.get(f"/api/threads/{thread_id}").json()
        assert detail["thread"]["title"] == "Dinner plans" and detail["messages"] == [] and detail["runs"] == []

        assert client.delete(f"/api/threads/{thread_id}").status_code == 204
        assert client.delete(f"/api/threads/{thread_id}").status_code == 404
        assert client.get(f"/api/threads/{thread_id}").status_code == 404
        assert client.patch(f"/api/threads/{thread_id}", json={"title": "x"}).status_code == 404
