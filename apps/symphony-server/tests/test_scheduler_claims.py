"""The scheduler against races and restarts: an edit or delete made while a run starts wins, a due time fires at most
once across a restart, and replanning never drops a run that is due."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from symphony_server.calendar_models import Automation, AutomationInput
from symphony_server.calendar_store import CalendarStore
from symphony_server.scheduler import Scheduler
from symphony_server.storage import AgentRunRecord
from test_calendar import Clock, FakeAgent, FakeNotes

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


class GatedAgent(FakeAgent):
    """Starts a task only when the test opens the gate, so the test can act while a run is starting."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def start_task(self, title: str, task: str) -> AgentRunRecord | str:
        await self.gate.wait()
        return await super().start_task(title, task)


class DyingAgent(FakeAgent):
    """Starts the task, then the server is killed before the scheduler hears back."""

    async def start_task(self, title: str, task: str) -> AgentRunRecord | str:
        await super().start_task(title, task)
        raise asyncio.CancelledError


def due_automation(store: CalendarStore, rrule: str | None = "FREQ=DAILY") -> Automation:
    given = AutomationInput(title="t", trigger="time", at=NOW + timedelta(seconds=1), rrule=rrule, timezone="UTC", action="agent_task", prompt="p")
    return store.add_automation(given, NOW)


def scheduler_for(store: CalendarStore, clock: Clock, agent: FakeAgent) -> Scheduler:
    scheduler = Scheduler(store, FakeNotes(), "UTC", clock)
    scheduler.agent = agent
    return scheduler


def test_a_deleted_automation_is_not_brought_back_by_the_run_that_was_starting() -> None:
    async def scenario() -> tuple[Automation | None, list[str]]:
        store, clock, agent = CalendarStore(None), Clock(NOW), GatedAgent()
        automation = due_automation(store)
        clock.now = NOW + timedelta(seconds=2)
        tick = asyncio.create_task(scheduler_for(store, clock, agent).tick())
        await asyncio.sleep(0)
        assert store.delete_automation(automation.id)
        agent.gate.set()
        await tick
        return store.automation(automation.id), [run.status for run in store.runs_after(0)]

    back, runs = asyncio.run(scenario())
    assert back is None
    assert runs == ["started"]  # the task that had already been asked for is still on record


def test_an_edit_made_while_a_run_starts_is_kept() -> None:
    async def scenario() -> Automation | None:
        store, clock, agent = CalendarStore(None), Clock(NOW), GatedAgent()
        automation = due_automation(store)
        clock.now = NOW + timedelta(seconds=2)
        tick = asyncio.create_task(scheduler_for(store, clock, agent).tick())
        await asyncio.sleep(0)
        edited = AutomationInput(**{**automation.model_dump(include=set(AutomationInput.model_fields)), "enabled": False, "prompt": "new"})
        store.update_automation(automation.id, edited, clock.now)
        agent.gate.set()
        await tick
        return store.automation(automation.id)

    after = asyncio.run(scenario())
    assert after is not None
    assert (after.enabled, after.prompt) == (False, "new")
    assert after.lastStatus == "started" and after.nextRunAt is not None and after.nextRunAt > NOW + timedelta(seconds=2)


def test_a_restart_while_a_run_starts_never_fires_it_twice(tmp_path: Path) -> None:
    path = tmp_path / "osmo.db"
    clock = Clock(NOW)
    first = CalendarStore(path)
    automation = due_automation(first, rrule=None)
    clock.now = NOW + timedelta(seconds=2)
    dying = DyingAgent()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler_for(first, clock, dying).tick())
    first.close()

    second = CalendarStore(path)
    agent = FakeAgent()
    scheduler = scheduler_for(second, clock, agent)
    interrupted = scheduler.reconcile()
    assert asyncio.run(scheduler.tick()) == []
    assert len(dying.tasks) + len(agent.tasks) == 1
    assert [run.status for run in interrupted] == ["error"] and "not run again" in interrupted[0].detail
    after = second.automation(automation.id)
    assert after is not None and after.lastStatus == "error"


def test_a_due_time_is_claimed_once() -> None:
    store, clock = CalendarStore(None), Clock(NOW)
    automation = due_automation(store)
    clock.now = NOW + timedelta(seconds=2)
    due = automation.nextRunAt
    assert due is not None
    assert store.claim(automation, due, NOW + timedelta(days=1), clock.now, "started", "starting") is not None
    # The same pass's stale copy, or another server on the same file: the claim is gone either way.
    assert store.claim(automation, due, NOW + timedelta(days=1), clock.now, "started", "starting") is None
    fresh = store.automation(automation.id)
    assert fresh is not None
    assert store.claim(fresh.model_copy(update={"nextRunAt": due}), due, None, clock.now, "started", "starting") is None


def test_replanning_keeps_a_run_that_is_due_and_has_not_fired() -> None:
    store, clock = CalendarStore(None), Clock(NOW)
    agent = FakeAgent()
    scheduler = scheduler_for(store, clock, agent)
    automation = due_automation(store)
    clock.now = NOW + timedelta(seconds=30)  # due, well inside MISSED_GRACE; the scheduler has not looked yet
    scheduler.replan()  # an event was added, or automations resumed
    kept = store.automation(automation.id)
    assert kept is not None and kept.nextRunAt == automation.nextRunAt
    asyncio.run(scheduler.tick())
    assert len(agent.tasks) == 1 and [run.status for run in store.runs_after(0)] == ["started"]


def test_a_calendar_from_before_claims_and_versions_opens_and_runs(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = Automation(**AutomationInput(title="t", trigger="time", at=NOW, timezone="UTC", action="notify", prompt="p").model_dump(), id="au-old", nextRunAt=NOW)
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE automations (id TEXT PRIMARY KEY, body TEXT NOT NULL);"
                         "CREATE TABLE automation_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, automation_id TEXT NOT NULL, body TEXT NOT NULL);")
        db.execute("INSERT INTO automations (id, body) VALUES (?, ?)", (old.id, old.model_dump_json(exclude={"version"})))
    db.close()
    store = CalendarStore(path)
    runs = asyncio.run(scheduler_for(store, Clock(NOW + timedelta(seconds=5)), FakeAgent()).tick())
    assert [(run.status, run.detail) for run in runs] == [("ok", "p")]
