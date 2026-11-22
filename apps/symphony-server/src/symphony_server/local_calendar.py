"""A local calendar with automations: events and the work Osmo does on a schedule, in SQLite.

Events carry an IANA zone and an optional RRULE (dateutil expands it on wall-clock time, so a weekly 2 PM stays
2 PM across DST). An automation fires on its own schedule (`time`: a moment, optionally repeating by RRULE) or
around an event (`event_start` / `event_end`, plus an offset in minutes), and does one thing: start a background
agent run on a new thread (`agent_task`), raise a desktop notification (`notify`) or append to a vault note
(`note_append`). `Scheduler` keeps each automation's `next_run_at`, sleeps until the earliest one and fires what is
due; one that was due more than `MISSED_GRACE` ago (the app was not running, the machine slept) is recorded as
skipped rather than run late. The scheduler only decides when: an agent task is a durable background agent run
(agents.AgentRuns over upstream/durable.py) on a thread of its own, so it shows in Tasks, can be stopped there, and
picks up where it left off after a restart. Its automation run stays `started` until that agent run finishes, and then
says `ok` or `error` (`Scheduler.settle`, called by AgentRuns, and `Scheduler.reconcile` at startup for runs that
finished or were given up while the server was down). The desktop app keeps the server, and so the scheduler, alive
from the tray when the window is closed; it never starts at login. `.ics` files import and export through icalendar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrule, rrulestr
from fastapi import APIRouter, HTTPException, Query, Request, Response
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent
from icalendar import vRecur
from core_harness import EventSink, Tool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from symphony_server.canvas import WidgetBoard, show
from symphony_server.storage import AgentRunRecord, AgentRunStatus
from symphony_server.vault import Note, WriteMode
from symphony_server.widgets import AgendaFooter, AgendaItem, AgendaProps, AgendaWidget

log = logging.getLogger("symphony.calendar")

# An automation due longer ago than this when the scheduler sees it is skipped, not run late.
MISSED_GRACE = timedelta(minutes=10)
# The scheduler looks again at least this often (an edit wakes it sooner).
SCHEDULER_MAX_SLEEP_S = 60.0
# The most occurrences one listing expands (a minutely RRULE over a year would be ~500k).
OCCURRENCES_MAX = 2000
RUNS_KEPT = 500

Trigger = Literal["time", "event_start", "event_end"]
Action = Literal["agent_task", "notify", "note_append"]
RunStatus = Literal["started", "ok", "error", "skipped"]
# What an agent task's automation run says once its agent run is over.
TASK_OUTCOMES: dict[AgentRunStatus, RunStatus] = {"completed": "ok", "failed": "error", "cancelled": "error"}
# Runs a calendar from before agent tasks were linked do not have `run_id`; it is added on open.
TASK_INDEX = "CREATE INDEX IF NOT EXISTS automation_runs_task ON automation_runs (run_id)"
# How much of a finished task's result its automation run keeps (the whole answer is in the task's thread).
TASK_DETAIL_MAX = 300


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {name!r}") from exc


def checked_rule(value: str | None) -> str | None:
    """An RRULE body (`FREQ=WEEKLY;BYDAY=SA`), with or without an `RRULE:` prefix; ValueError when dateutil cannot read it."""
    if value is None or not value.strip():
        return None
    body = value.strip().removeprefix("RRULE:")
    if "\n" in body or "DTSTART" in body.upper():
        raise ValueError("give the RRULE alone (no DTSTART or extra lines)")
    rrulestr(body, dtstart=datetime(2026, 1, 1))
    return body


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventInput(_Strict):
    title: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime
    timezone: str = "UTC"
    rrule: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        zone(value)
        return value

    @field_validator("rrule")
    @classmethod
    def _rule(cls, value: str | None) -> str | None:
        return checked_rule(value)

    @model_validator(mode="after")
    def _order(self) -> EventInput:
        if self.end <= self.start:
            raise ValueError("an event must end after it starts")
        return self


class CalendarEvent(EventInput):
    id: str
    uid: str


class Occurrence(BaseModel):
    """One showing of an event in a range (a repeating event has many)."""

    eventId: str
    title: str
    start: datetime
    end: datetime
    rrule: str | None = None
    location: str | None = None


class AutomationInput(_Strict):
    title: str = Field(min_length=1, max_length=120)
    trigger: Trigger
    # `time`: the first (or only) moment; repeats by `rrule` in `timezone`.
    at: AwareDatetime | None = None
    rrule: str | None = Field(default=None, max_length=300)
    timezone: str = "UTC"
    # `event_start` / `event_end`: the event, and minutes after (negative: before) that moment.
    eventId: str | None = None
    offsetMinutes: int = Field(default=0, ge=-7 * 24 * 60, le=7 * 24 * 60)
    action: Action
    prompt: str = Field(min_length=1, max_length=4000)
    # `note_append`: the vault note to add to (vault.py checks the path).
    notePath: str | None = Field(default=None, max_length=300)
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        zone(value)
        return value

    @field_validator("rrule")
    @classmethod
    def _rule(cls, value: str | None) -> str | None:
        return checked_rule(value)

    @model_validator(mode="after")
    def _complete(self) -> AutomationInput:
        if self.trigger == "time" and self.at is None:
            raise ValueError("a time automation needs `at`")
        if self.trigger != "time" and self.eventId is None:
            raise ValueError(f"an {self.trigger} automation needs `eventId`")
        if self.action == "note_append" and not self.notePath:
            raise ValueError("note_append needs `notePath`")
        return self


class Automation(AutomationInput):
    id: str
    lastRunAt: datetime | None = None
    lastStatus: RunStatus | None = None
    nextRunAt: datetime | None = None


class AutomationRun(BaseModel):
    id: int
    automationId: str
    title: str
    at: datetime
    status: RunStatus
    detail: str = ""
    runId: str | None = None
    threadId: str | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id TEXT NOT NULL,
    -- The agent run (task) an agent_task automation started, if any.
    run_id TEXT,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calendar_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def expand(event: EventInput, window_start: datetime, window_end: datetime, limit: int = OCCURRENCES_MAX) -> list[tuple[datetime, datetime]]:
    """The (start, end) of each showing of `event` that overlaps [window_start, window_end), in the event's zone."""
    tz = zone(event.timezone)
    start = event.start.astimezone(tz)
    length = event.end - event.start
    if event.rrule is None:
        return [(start, start + length)] if start < window_end and start + length > window_start else []
    rule = rrulestr(event.rrule, dtstart=start)
    assert isinstance(rule, rrule)
    shown: list[tuple[datetime, datetime]] = []
    for begin in rule.xafter(window_start - length, count=limit, inc=False):
        if begin >= window_end:
            break
        shown.append((begin, begin + length))
    return shown


def first_after(event: EventInput, after: datetime, shift: timedelta) -> datetime | None:
    """The first moment `showing start + shift` that is later than `after` (shift: duration and offset)."""
    tz = zone(event.timezone)
    start = event.start.astimezone(tz)
    if event.rrule is None:
        moment = start + shift
        return moment if moment > after else None
    rule = rrulestr(event.rrule, dtstart=start)
    assert isinstance(rule, rrule)
    begin = rule.after(after - shift, inc=False)
    return begin + shift if begin is not None else None


def next_run(automation: AutomationInput, after: datetime, event: EventInput | None) -> datetime | None:
    """When `automation` fires next, strictly after `after`; None when it never will again."""
    if automation.trigger == "time":
        assert automation.at is not None
        tz = zone(automation.timezone)
        at = automation.at.astimezone(tz)
        if automation.rrule is None:
            return at if at > after else None
        rule = rrulestr(automation.rrule, dtstart=at)
        assert isinstance(rule, rrule)
        found = rule.after(after, inc=False)
        return found
    if event is None:
        return None
    shift = timedelta(minutes=automation.offsetMinutes) + (event.end - event.start if automation.trigger == "event_end" else timedelta())
    return first_after(event, after, shift)


class CalendarStore:
    """Events, automations, their runs and the pause switch, in SQLite (thread-safe; the API runs it in workers)."""

    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self.lock = threading.Lock()
        with self.transaction() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(automation_runs)")}
            if "run_id" not in columns:
                db.execute("ALTER TABLE automation_runs ADD COLUMN run_id TEXT")
            db.execute(TASK_INDEX)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock, self.db:
            yield self.db

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ events

    def add_event(self, given: EventInput, uid: str | None = None) -> CalendarEvent:
        event = CalendarEvent(**given.model_dump(), id=f"ev-{secrets.token_hex(6)}", uid=uid or f"{secrets.token_hex(12)}@osmo")
        with self.transaction() as db:
            db.execute("INSERT INTO calendar_events (id, uid, body) VALUES (?, ?, ?)", (event.id, event.uid, event.model_dump_json()))
        return event

    def update_event(self, event_id: str, given: EventInput) -> CalendarEvent | None:
        current = self.event(event_id)
        if current is None:
            return None
        event = CalendarEvent(**given.model_dump(), id=current.id, uid=current.uid)
        with self.transaction() as db:
            db.execute("UPDATE calendar_events SET body = ? WHERE id = ?", (event.model_dump_json(), event.id))
        return event

    def event(self, event_id: str) -> CalendarEvent | None:
        with self.transaction() as db:
            row = db.execute("SELECT body FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        return CalendarEvent.model_validate_json(row[0]) if row else None

    def event_by_uid(self, uid: str) -> CalendarEvent | None:
        with self.transaction() as db:
            row = db.execute("SELECT body FROM calendar_events WHERE uid = ?", (uid,)).fetchone()
        return CalendarEvent.model_validate_json(row[0]) if row else None

    def events(self) -> list[CalendarEvent]:
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM calendar_events").fetchall()
        return [CalendarEvent.model_validate_json(row[0]) for row in rows]

    def delete_event(self, event_id: str) -> bool:
        with self.transaction() as db:
            gone = db.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,)).rowcount > 0
        return gone

    def occurrences(self, start: datetime, end: datetime) -> list[Occurrence]:
        shown = [
            Occurrence(eventId=event.id, title=event.title, start=begin, end=finish, rrule=event.rrule, location=event.location)
            for event in self.events()
            for begin, finish in expand(event, start, end)
        ]
        return sorted(shown, key=lambda occurrence: occurrence.start)[:OCCURRENCES_MAX]

    # ------------------------------------------------------------ automations

    def add_automation(self, given: AutomationInput, now: datetime) -> Automation:
        automation = Automation(**given.model_dump(), id=f"au-{secrets.token_hex(6)}")
        automation.nextRunAt = next_run(automation, now, self.event(automation.eventId) if automation.eventId else None)
        self.save_automation(automation)
        return automation

    def update_automation(self, automation_id: str, given: AutomationInput, now: datetime) -> Automation | None:
        current = self.automation(automation_id)
        if current is None:
            return None
        automation = Automation(**given.model_dump(), id=current.id, lastRunAt=current.lastRunAt, lastStatus=current.lastStatus)
        automation.nextRunAt = next_run(automation, now, self.event(automation.eventId) if automation.eventId else None)
        self.save_automation(automation)
        return automation

    def save_automation(self, automation: Automation) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO automations (id, body) VALUES (?, ?)", (automation.id, automation.model_dump_json()))

    def automation(self, automation_id: str) -> Automation | None:
        with self.transaction() as db:
            row = db.execute("SELECT body FROM automations WHERE id = ?", (automation_id,)).fetchone()
        return Automation.model_validate_json(row[0]) if row else None

    def automations(self) -> list[Automation]:
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM automations").fetchall()
        return sorted((Automation.model_validate_json(row[0]) for row in rows), key=lambda a: (a.nextRunAt is None, a.nextRunAt or datetime.max.replace(tzinfo=UTC)))

    def delete_automation(self, automation_id: str) -> bool:
        with self.transaction() as db:
            gone = db.execute("DELETE FROM automations WHERE id = ?", (automation_id,)).rowcount > 0
        return gone

    def add_run(self, automation: Automation, at: datetime, status: RunStatus, detail: str = "", run_id: str | None = None, thread_id: str | None = None) -> AutomationRun:
        with self.transaction() as db:
            cursor = db.execute("INSERT INTO automation_runs (automation_id, run_id, body) VALUES (?, ?, '{}')", (automation.id, run_id))
            assert cursor.lastrowid is not None
            run = AutomationRun(id=cursor.lastrowid, automationId=automation.id, title=automation.title, at=at, status=status, detail=detail, runId=run_id, threadId=thread_id)
            db.execute("UPDATE automation_runs SET body = ? WHERE id = ?", (run.model_dump_json(), run.id))
            db.execute("DELETE FROM automation_runs WHERE id <= ?", (run.id - RUNS_KEPT,))
        return run

    def runs_after(self, after: int, limit: int = 50) -> list[AutomationRun]:
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM automation_runs WHERE id > ? ORDER BY id LIMIT ?", (after, limit)).fetchall()
        return [AutomationRun.model_validate_json(row[0]) for row in rows]

    def task_runs(self, limit: int = RUNS_KEPT) -> list[AutomationRun]:
        """The latest automation runs that started an agent task, newest first."""
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM automation_runs WHERE run_id IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [AutomationRun.model_validate_json(row[0]) for row in rows]

    def started(self) -> list[AutomationRun]:
        """Automation runs whose agent task has not finished yet."""
        return [run for run in self.task_runs() if run.status == "started"]

    def settle(self, task_id: str, status: RunStatus, detail: str) -> AutomationRun | None:
        """Record how the agent task `task_id` ended on its automation run (and on the automation, if it is its
        latest run); None when no automation started that task or it was already settled."""
        with self.transaction() as db:
            row = db.execute("SELECT body FROM automation_runs WHERE run_id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            run = AutomationRun.model_validate_json(row[0])
            if run.status != "started":
                return None
            run.status, run.detail = status, detail
            db.execute("UPDATE automation_runs SET body = ? WHERE run_id = ?", (run.model_dump_json(), task_id))
        automation = self.automation(run.automationId)
        if automation is not None and automation.lastRunAt == run.at and automation.lastStatus == "started":
            automation.lastStatus = status
            self.save_automation(automation)
        return run

    def last_run_id(self) -> int:
        with self.transaction() as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) FROM automation_runs").fetchone()
        return int(row[0])

    @property
    def paused(self) -> bool:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM calendar_settings WHERE key = 'paused'").fetchone()
        return bool(row and row[0] == "1")

    def set_paused(self, paused: bool) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO calendar_settings (key, value) VALUES ('paused', ?)", ("1" if paused else "0",))

    # ------------------------------------------------------------ .ics

    def import_ics(self, data: bytes) -> int:
        """Add (or update, by UID) every VEVENT of an .ics file; how many were taken."""
        taken = 0
        for component in ICalendar.from_ical(data).walk("VEVENT"):
            parsed = ics_event(component)
            if parsed is None:
                continue
            given, uid = parsed
            existing = self.event_by_uid(uid)
            if existing is not None:
                self.update_event(existing.id, given)
            else:
                self.add_event(given, uid)
            taken += 1
        return taken

    def export_ics(self) -> bytes:
        cal = ICalendar()
        cal.add("prodid", "-//Osmo//Calendar//EN")
        cal.add("version", "2.0")
        for event in self.events():
            item = IEvent()
            item.add("uid", event.uid)
            item.add("summary", event.title)
            tz = zone(event.timezone)
            item.add("dtstart", event.start.astimezone(tz))
            item.add("dtend", event.end.astimezone(tz))
            item.add("dtstamp", datetime.now(UTC))
            if event.rrule:
                item.add("rrule", vRecur.from_ical(event.rrule))
            if event.location:
                item.add("location", event.location)
            if event.notes:
                item.add("description", event.notes)
            cal.add_component(item)
        return bytes(cal.to_ical())


def ics_event(component: Any) -> tuple[EventInput, str] | None:
    """An .ics VEVENT as an event and its UID; None for what Osmo cannot place (no start)."""
    if component.get("dtstart") is None:
        return None
    start = component.decoded("dtstart")
    end = component.decoded("dtend") if component.get("dtend") is not None else None
    if isinstance(start, datetime):
        tzname = str(start.tzinfo.key) if isinstance(start.tzinfo, ZoneInfo) else "UTC"
        start_at = start if start.tzinfo is not None else start.replace(tzinfo=UTC)
        end_at = end if isinstance(end, datetime) else start_at + timedelta(hours=1)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=start_at.tzinfo)
    elif isinstance(start, date):
        tzname = "UTC"
        start_at = datetime(start.year, start.month, start.day, tzinfo=UTC)
        end_at = datetime(end.year, end.month, end.day, tzinfo=UTC) if isinstance(end, date) else start_at + timedelta(days=1)
    else:
        return None
    rule = component.get("rrule")
    try:
        given = EventInput(
            title=str(component.get("summary") or "Untitled")[:200],
            start=start_at,
            end=end_at if end_at > start_at else start_at + timedelta(minutes=30),
            timezone=tzname,
            rrule=rule.to_ical().decode() if rule is not None else None,
            location=str(component.get("location"))[:300] if component.get("location") else None,
            notes=str(component.get("description"))[:4000] if component.get("description") else None,
        )
    except (ValidationError, ValueError):
        return None
    return given, str(component.get("uid") or f"{secrets.token_hex(12)}@osmo")


# ------------------------------------------------------------ the scheduler

Clock = Callable[[], datetime]


class AutomationAgent(Protocol):
    """The durable background agent (agents.AgentRuns), as automations use it."""

    async def start_task(self, title: str, task: str) -> AgentRunRecord | str: ...

    def get(self, run_id: str) -> AgentRunRecord | None: ...


class NoteWriter(Protocol):
    """The vaults (vault.VaultIndex), for `note_append`."""

    def write(self, relative: str, text: str, mode: WriteMode, vault: str | None = None) -> Note: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def automation_task(automation: Automation, event: CalendarEvent | None, at: datetime, tz: ZoneInfo) -> str:
    """What an agent task automation asks the background agent: its prompt, and where it comes from."""
    local = at.astimezone(tz)
    hour = local.hour % 12 or 12
    origin = f"This is the Osmo automation \u201c{automation.title}\u201d, run on {local:%a %b} {local.day} at {hour}:{local:%M %p}"
    if event is not None and automation.trigger != "time":
        edge = "start" if automation.trigger == "event_start" else "end"
        origin += f", at the {edge} of the calendar event \u201c{event.title}\u201d"
    return f"{automation.prompt}\n\n({origin}. Nobody is watching it run: a step that needs approval waits for the user in Tasks.)"


class Scheduler:
    """Fires automations at their `nextRunAt`. `tick` is one pass (what tests drive with a fake clock)."""

    def __init__(self, store: CalendarStore, notes: NoteWriter, timezone: str, clock: Clock = utc_now) -> None:
        self.store, self.notes, self.clock = store, notes, clock
        # The background agent; the app sets it once its agent runs exist (they need the tools, which need this).
        self.agent: AutomationAgent | None = None
        self.tz = zone(timezone)
        self.wakeup = asyncio.Event()

    @property
    def running(self) -> int:
        """Agent tasks that automations started and that have not finished."""
        return len(self.store.started())

    def wake(self) -> None:
        """Something changed (an edit, a pause): look again now rather than at the next planned time."""
        self.wakeup.set()

    async def fire(self, automation: Automation, now: datetime) -> AutomationRun:
        """Run one automation now (a due one, or Run now) and record it; never raises."""
        try:
            status, detail, run_id, thread_id = await self.perform(automation, now)
        except Exception as exc:  # an automation failing is recorded, never fatal to the scheduler
            log.warning("automation %s failed: %s", automation.id, exc)
            status, detail, run_id, thread_id = "error", str(exc) or type(exc).__name__, None, None
        return self.store.add_run(automation, now, status, detail, run_id, thread_id)

    async def perform(self, automation: Automation, now: datetime) -> tuple[RunStatus, str, str | None, str | None]:
        """What a due automation does: a background agent task on a new thread (it stays `started` until the task
        is over), a note append, or a notification (the tray shows it)."""
        if automation.action == "agent_task":
            if self.agent is None:
                return "error", "the background agent is not running", None, None
            event = self.store.event(automation.eventId) if automation.eventId else None
            started = await self.agent.start_task(automation.title, automation_task(automation, event, now, self.tz))
            if isinstance(started, str):
                return "error", started, None, None
            return "started", "working in Tasks", started.runId, started.threadId
        if automation.action == "note_append":
            assert automation.notePath is not None
            stamp = now.astimezone(self.tz).strftime("%Y-%m-%d %H:%M")
            note = await asyncio.to_thread(self.notes.write, automation.notePath, f"\n## {stamp}\n\n{automation.prompt}\n", "append")
            return "ok", f"appended to {note.path}", None, None
        return "ok", automation.prompt, None, None

    def settle(self, task: AgentRunRecord) -> AutomationRun | None:
        """An agent run changed: if it is an automation's task and it is over, record how it ended."""
        outcome = TASK_OUTCOMES.get(task.status)
        if outcome is None:
            return None
        detail = (task.result or task.status).strip()
        if len(detail) > TASK_DETAIL_MAX:
            detail = detail[: TASK_DETAIL_MAX - 1] + "\u2026"
        return self.store.settle(task.runId, outcome, detail)

    def reconcile(self) -> list[AutomationRun]:
        """At startup: settle the tasks that finished, or were given up, while no one was listening."""
        settled: list[AutomationRun] = []
        if self.agent is None:
            return []
        for run in self.store.started():
            assert run.runId is not None
            task = self.agent.get(run.runId)
            done = self.store.settle(run.runId, "error", "the task is gone") if task is None else self.settle(task)
            if done is not None:
                settled.append(done)
        return settled

    async def tick(self) -> list[AutomationRun]:
        """Run (or skip, when missed or paused) every automation that is due, and plan each one's next run."""
        now = self.clock()
        paused = self.store.paused
        done: list[AutomationRun] = []
        for automation in self.store.automations():
            due = automation.nextRunAt
            if not automation.enabled or due is None or due > now:
                continue
            run: AutomationRun | None = None
            if paused:
                pass  # nothing runs while paused, and nothing is owed when it resumes
            elif now - due > MISSED_GRACE:
                run = self.store.add_run(automation, now, "skipped", f"missed at {due.isoformat()} (Osmo was not running)")
            else:
                run = await self.fire(automation, now)
            if run is not None:
                automation.lastRunAt, automation.lastStatus = run.at, run.status
                done.append(run)
            event = self.store.event(automation.eventId) if automation.eventId else None
            automation.nextRunAt = next_run(automation, now, event)
            self.store.save_automation(automation)
        return done

    def seconds_to_next(self) -> float:
        now = self.clock()
        upcoming = [a.nextRunAt for a in self.store.automations() if a.enabled and a.nextRunAt is not None]
        if not upcoming:
            return SCHEDULER_MAX_SLEEP_S
        return max(0.0, min(SCHEDULER_MAX_SLEEP_S, (min(upcoming) - now).total_seconds()))

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # a broken row must not stop every other automation
                log.exception("scheduler pass failed: %s", exc)
            self.wakeup.clear()
            try:
                async with asyncio.timeout(self.seconds_to_next()):
                    await self.wakeup.wait()
            except TimeoutError:
                pass

    def replan(self) -> None:
        """Recompute every automation's next run from now (after an event moved, or on resume)."""
        now = self.clock()
        for automation in self.store.automations():
            event = self.store.event(automation.eventId) if automation.eventId else None
            automation.nextRunAt = next_run(automation, now, event)
            self.store.save_automation(automation)
        self.wake()


# ------------------------------------------------------------ the API


class Tick(BaseModel):
    """One planned run of an automation inside a listed range (the Calendar grid marks them)."""

    automationId: str
    title: str
    at: datetime


class EventList(BaseModel):
    occurrences: list[Occurrence]
    events: list[CalendarEvent]
    ticks: list[Tick]


def ticks(store: CalendarStore, start: datetime, end: datetime, limit: int = 400) -> list[Tick]:
    """Every planned run of every enabled automation in [start, end), soonest first."""
    planned: list[Tick] = []
    for automation in store.automations():
        if not automation.enabled:
            continue
        event = store.event(automation.eventId) if automation.eventId else None
        moment = next_run(automation, start - timedelta(microseconds=1), event)
        while moment is not None and moment < end and len(planned) < limit:
            planned.append(Tick(automationId=automation.id, title=automation.title, at=moment))
            moment = next_run(automation, moment, event)
    return sorted(planned, key=lambda tick: tick.at)


class AutomationList(BaseModel):
    automations: list[Automation]


class RunList(BaseModel):
    runs: list[AutomationRun]
    last: int


class SchedulerStatus(BaseModel):
    paused: bool
    running: int
    next: Automation | None
    last: int


class PauseBody(_Strict):
    paused: bool


class Imported(BaseModel):
    imported: int


class TaskLink(BaseModel):
    """An agent task an automation started: what the Tasks screen shows beside it, and where it opens in the calendar."""

    runId: str
    threadId: str | None
    automationId: str
    title: str
    at: datetime
    status: RunStatus
    # None when the automation was deleted since.
    trigger: Trigger | None = None
    eventId: str | None = None
    eventTitle: str | None = None


class TaskLinks(BaseModel):
    links: list[TaskLink]


def task_links(store: CalendarStore) -> list[TaskLink]:
    links: list[TaskLink] = []
    for run in store.task_runs():
        assert run.runId is not None
        automation = store.automation(run.automationId)
        event = store.event(automation.eventId) if automation is not None and automation.eventId else None
        links.append(
            TaskLink(
                runId=run.runId, threadId=run.threadId, automationId=run.automationId, title=run.title, at=run.at, status=run.status,
                trigger=automation.trigger if automation is not None else None, eventId=event.id if event else None, eventTitle=event.title if event else None,
            )
        )
    return links


def create_calendar_router(store: CalendarStore, scheduler: Scheduler) -> APIRouter:
    """Events, automations and the scheduler's state for the Calendar screen and the desktop tray."""
    router = APIRouter(prefix="/api", tags=["calendar"])

    def missing(kind: str, item_id: str) -> HTTPException:
        return HTTPException(404, f"no {kind} {item_id!r}")

    @router.get("/calendar/events")
    async def list_events(start: AwareDatetime, end: AwareDatetime) -> EventList:
        if end <= start or end - start > timedelta(days=400):
            raise HTTPException(400, "pick a range of up to 400 days that ends after it starts")
        occurrences = await asyncio.to_thread(store.occurrences, start, end)
        ids = {occurrence.eventId for occurrence in occurrences}
        planned = await asyncio.to_thread(ticks, store, start, end)
        return EventList(occurrences=occurrences, events=[event for event in store.events() if event.id in ids], ticks=planned)

    @router.post("/calendar/events", status_code=201)
    async def add_event(body: EventInput) -> CalendarEvent:
        event = store.add_event(body)
        scheduler.replan()
        return event

    @router.put("/calendar/events/{event_id}")
    async def update_event(event_id: str, body: EventInput) -> CalendarEvent:
        event = store.update_event(event_id, body)
        if event is None:
            raise missing("event", event_id)
        scheduler.replan()
        return event

    @router.delete("/calendar/events/{event_id}", status_code=204)
    async def delete_event(event_id: str) -> None:
        if not store.delete_event(event_id):
            raise missing("event", event_id)
        scheduler.replan()

    @router.post("/calendar/import")
    async def import_ics(request: Request) -> Imported:
        data = await request.body()
        try:
            imported = await asyncio.to_thread(store.import_ics, data)
        except ValueError as exc:
            raise HTTPException(400, f"not an .ics calendar: {exc}") from exc
        scheduler.replan()
        return Imported(imported=imported)

    @router.get("/calendar/export.ics")
    async def export_ics() -> Response:
        return Response(await asyncio.to_thread(store.export_ics), media_type="text/calendar")

    @router.get("/automations")
    async def list_automations() -> AutomationList:
        return AutomationList(automations=store.automations())

    @router.post("/automations", status_code=201)
    async def add_automation(body: AutomationInput) -> Automation:
        if body.eventId is not None and store.event(body.eventId) is None:
            raise missing("event", body.eventId)
        automation = store.add_automation(body, scheduler.clock())
        scheduler.wake()
        return automation

    @router.put("/automations/{automation_id}")
    async def update_automation(automation_id: str, body: AutomationInput) -> Automation:
        automation = store.update_automation(automation_id, body, scheduler.clock())
        if automation is None:
            raise missing("automation", automation_id)
        scheduler.wake()
        return automation

    @router.delete("/automations/{automation_id}", status_code=204)
    async def delete_automation(automation_id: str) -> None:
        if not store.delete_automation(automation_id):
            raise missing("automation", automation_id)

    @router.post("/automations/{automation_id}/run")
    async def run_now(automation_id: str) -> AutomationRun:
        automation = store.automation(automation_id)
        if automation is None:
            raise missing("automation", automation_id)
        run = await scheduler.fire(automation, scheduler.clock())
        automation.lastRunAt, automation.lastStatus = run.at, run.status
        store.save_automation(automation)
        return run

    @router.get("/automations/runs")
    async def runs(after: int = Query(0, ge=0)) -> RunList:
        listed = store.runs_after(after)
        return RunList(runs=listed, last=listed[-1].id if listed else max(after, 0))

    @router.get("/automations/tasks")
    async def tasks() -> TaskLinks:
        """The agent tasks automations started, newest first (the Tasks screen links each to its automation and event)."""
        return TaskLinks(links=await asyncio.to_thread(task_links, store))

    @router.get("/automations/status")
    async def status() -> SchedulerStatus:
        upcoming = [a for a in store.automations() if a.enabled and a.nextRunAt is not None]
        return SchedulerStatus(paused=store.paused, running=scheduler.running, next=upcoming[0] if upcoming else None, last=store.last_run_id())

    @router.post("/automations/pause")
    async def pause(body: PauseBody) -> SchedulerStatus:
        store.set_paused(body.paused)
        if not body.paused:
            scheduler.replan()
        scheduler.wake()
        return await status()

    return router



# ------------------------------------------------------------ tools


def local_zone_name() -> str:
    """The machine's IANA zone (TZ, or the /etc/localtime link); UTC when neither names one."""
    named = os.environ.get("TZ", "").lstrip(":")
    if named:
        try:
            zone(named)
            return named
        except ValueError:
            pass
    link = Path("/etc/localtime")
    if link.is_symlink():
        target = str(link.resolve())
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    return "UTC"


def clock_label(moment: datetime) -> str:
    """`2:00` / `14:05` style: hours and minutes, 12-hour, no AM/PM (the agenda's column)."""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d}"


def agenda(store: CalendarStore, day: date, days: int, tz: ZoneInfo, now: datetime) -> AgendaProps:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=days)
    items: list[AgendaItem] = []
    for occurrence in store.occurrences(start, end)[:40]:
        begin, finish = occurrence.start.astimezone(tz), occurrence.end.astimezone(tz)
        state: Literal["past", "now", "upcoming"] = "past" if finish <= now else "now" if begin <= now else "upcoming"
        time_label = clock_label(begin) if days == 1 else f"{begin:%a} {clock_label(begin)}"
        items.append(AgendaItem(time=time_label, title=occurrence.title[:200], state=state, tag={"past": "done", "now": "now", "upcoming": None}[state]))
    upcoming = [a for a in store.automations() if a.enabled and a.nextRunAt is not None]
    footer = None
    if upcoming and upcoming[0].nextRunAt is not None:
        at = upcoming[0].nextRunAt.astimezone(tz)
        minutes = int((at - now).total_seconds() // 60)
        detail = f"{at:%-I:%M %p}" + (f" · in {minutes} min" if 0 <= minutes < 120 else f" · {at:%a %b %-d}" if at.date() != now.date() else "")
        footer = AgendaFooter(label="Next automation", value=upcoming[0].title[:120], detail=detail[:60])
    title = "Today" if day == now.date() and days == 1 else f"{day:%a %b %-d}" + (f" + {days - 1} days" if days > 1 else "")
    return AgendaProps(title=title, subtitle=f"{day:%a %b %-d}", items=items, footer=footer)


class EventsArgs(_Strict):
    day: date | None = Field(default=None, description="First day to list (YYYY-MM-DD); omit for today.")
    days: int = Field(default=1, ge=1, le=31)
    show: bool = Field(default=False, description="Also show the days as an agenda card on screen.")


class AddEventArgs(_Strict):
    title: str = Field(min_length=1, max_length=200)
    start: datetime = Field(description="Local start time, e.g. 2026-11-22T11:00 (the user's time zone).")
    minutes: int = Field(default=60, ge=5, le=24 * 60, description="How long it lasts.")
    rrule: str | None = Field(default=None, description="An RRULE to repeat it, e.g. FREQ=WEEKLY;BYDAY=SA.")
    location: str | None = Field(default=None, max_length=300)


class CreateAutomationArgs(_Strict):
    title: str = Field(min_length=1, max_length=120)
    trigger: Trigger
    at: datetime | None = Field(default=None, description="time trigger: local time of the first run, e.g. 2026-11-22T08:00.")
    rrule: str | None = Field(default=None, description="time trigger: repeat by this RRULE, e.g. FREQ=DAILY.")
    eventId: str | None = Field(default=None, description="event_start/event_end trigger: the event id from calendar_events.")
    offsetMinutes: int = Field(default=0, description="Minutes after (negative: before) the event moment.")
    action: Action
    prompt: str = Field(min_length=1, max_length=4000, description="agent_task: the task; notify: the message; note_append: the text.")
    notePath: str | None = Field(default=None, description="note_append: the vault note path.")


def localized(moment: datetime, tz: ZoneInfo) -> datetime:
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment


class CalendarEventsTool(Tool):
    def __init__(self, store: CalendarStore, board: WidgetBoard, timezone: str, clock: Clock = utc_now) -> None:
        super().__init__(
            name="calendar_events",
            description="List the user's local calendar for a day or a few (events with ids, and automations due). show=true also puts an agenda card on screen.",
            parameters=EventsArgs.model_json_schema(),
        )
        self.store, self.board, self.tz, self.clock = store, board, zone(timezone), clock

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        try:
            command = EventsArgs.model_validate(args)
        except ValidationError as exc:
            return json.dumps({"error": str(exc)})
        now = self.clock().astimezone(self.tz)
        day = command.day or now.date()
        start = datetime(day.year, day.month, day.day, tzinfo=self.tz)
        listed = await asyncio.to_thread(self.store.occurrences, start, start + timedelta(days=command.days))
        result: dict[str, Any] = {
            "timezone": str(self.tz.key),
            "events": [
                {"id": o.eventId, "title": o.title, "start": o.start.astimezone(self.tz).isoformat(), "end": o.end.astimezone(self.tz).isoformat(), "repeats": o.rrule}
                for o in listed
            ],
            "automations": [
                {"id": a.id, "title": a.title, "next": a.nextRunAt.astimezone(self.tz).isoformat() if a.nextRunAt else None, "action": a.action, "enabled": a.enabled}
                for a in self.store.automations()
            ],
        }
        if command.show:
            shown = json.loads(await show(self.board, sink, AgendaWidget(type="agenda", props=agenda(self.store, day, command.days, self.tz, now))))
            result["shown"] = shown
        return json.dumps(result)


class CalendarAddEventTool(Tool):
    def __init__(self, store: CalendarStore, scheduler: Scheduler, timezone: str) -> None:
        super().__init__(
            name="calendar_add_event",
            description="Add an event to the user's local calendar (times are in the user's time zone).",
            parameters=AddEventArgs.model_json_schema(),
        )
        self.store, self.scheduler, self.timezone = store, scheduler, timezone

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        del sink
        try:
            command = AddEventArgs.model_validate(args)
            start = localized(command.start, zone(self.timezone))
            given = EventInput(title=command.title, start=start, end=start + timedelta(minutes=command.minutes), timezone=self.timezone, rrule=command.rrule, location=command.location)
        except (ValidationError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        event = self.store.add_event(given)
        self.scheduler.replan()
        return json.dumps({"added": event.id, "title": event.title, "start": event.start.isoformat()})


class AutomationCreateTool(Tool):
    def __init__(self, store: CalendarStore, scheduler: Scheduler, timezone: str) -> None:
        super().__init__(
            name="automation_create",
            description=(
                "Schedule an automation that runs from the tray even when the window is closed: at a time (optionally repeating), "
                "or at an event's start or end. It starts a background agent task, shows a notification, or appends to a vault note. "
                "The user approves it first."
            ),
            parameters=CreateAutomationArgs.model_json_schema(),
        )
        self.store, self.scheduler, self.timezone = store, scheduler, timezone

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        del sink
        try:
            command = CreateAutomationArgs.model_validate(args)
            tz = zone(self.timezone)
            given = AutomationInput(
                **command.model_dump(exclude={"at"}),
                at=localized(command.at, tz) if command.at is not None else None,
                timezone=self.timezone,
            )
        except (ValidationError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        if given.eventId is not None and self.store.event(given.eventId) is None:
            return json.dumps({"error": f"no event {given.eventId!r}; list events with calendar_events"})
        automation = self.store.add_automation(given, self.scheduler.clock())
        self.scheduler.wake()
        next_at = automation.nextRunAt.astimezone(tz).isoformat() if automation.nextRunAt else None
        return json.dumps({"created": automation.id, "title": automation.title, "next": next_at})


def calendar_tools(store: CalendarStore, scheduler: Scheduler, board: WidgetBoard, timezone: str) -> list[Tool]:
    return [
        CalendarEventsTool(store, board, timezone, scheduler.clock),
        CalendarAddEventTool(store, scheduler, timezone),
        AutomationCreateTool(store, scheduler, timezone),
    ]


CALENDAR_TOOL_NAMES = frozenset({"calendar_events", "calendar_add_event", "automation_create"})
