"""The calendar in SQLite: events, automations, their runs and the pause switch; `.ics` import and export."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent
from icalendar import vRecur

from symphony_server.calendar_ics import ics_event
from symphony_server.calendar_models import Automation, AutomationInput, AutomationRun, CalendarEvent, EventInput, Occurrence, RunStatus, zone
from symphony_server.recurrence import OCCURRENCES_MAX, expand, next_run

RUNS_KEPT = 500

# Runs a calendar from before agent tasks were linked do not have `run_id`; it is added on open.
TASK_INDEX = "CREATE INDEX IF NOT EXISTS automation_runs_task ON automation_runs (run_id)"
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
        """The database under the store's lock, committed on success and rolled back on error."""
        with self.lock, self.db:
            yield self.db

    def close(self) -> None:
        """Close the database."""
        self.db.close()

    # ------------------------------------------------------------ events

    def add_event(self, given: EventInput, uid: str | None = None) -> CalendarEvent:
        """Store a new event (a new UID unless an import gives one)."""
        event = CalendarEvent(**given.model_dump(), id=f"ev-{secrets.token_hex(6)}", uid=uid or f"{secrets.token_hex(12)}@osmo")
        with self.transaction() as db:
            db.execute("INSERT INTO calendar_events (id, uid, body) VALUES (?, ?, ?)", (event.id, event.uid, event.model_dump_json()))
        return event

    def update_event(self, event_id: str, given: EventInput) -> CalendarEvent | None:
        """Replace an event's fields, keeping its id and UID; None when there is no such event."""
        current = self.event(event_id)
        if current is None:
            return None
        event = CalendarEvent(**given.model_dump(), id=current.id, uid=current.uid)
        with self.transaction() as db:
            db.execute("UPDATE calendar_events SET body = ? WHERE id = ?", (event.model_dump_json(), event.id))
        return event

    def event(self, event_id: str) -> CalendarEvent | None:
        """The event `event_id`, or None."""
        with self.transaction() as db:
            row = db.execute("SELECT body FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        return CalendarEvent.model_validate_json(row[0]) if row else None

    def event_by_uid(self, uid: str) -> CalendarEvent | None:
        """The event with iCalendar UID `uid`, or None."""
        with self.transaction() as db:
            row = db.execute("SELECT body FROM calendar_events WHERE uid = ?", (uid,)).fetchone()
        return CalendarEvent.model_validate_json(row[0]) if row else None

    def events(self) -> list[CalendarEvent]:
        """Every event."""
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM calendar_events").fetchall()
        return [CalendarEvent.model_validate_json(row[0]) for row in rows]

    def delete_event(self, event_id: str) -> bool:
        """Delete an event; False when there was none."""
        with self.transaction() as db:
            gone = db.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,)).rowcount > 0
        return gone

    def occurrences(self, start: datetime, end: datetime) -> list[Occurrence]:
        """Every showing of every event that overlaps [start, end), in order."""
        shown = [
            Occurrence(eventId=event.id, title=event.title, start=begin, end=finish, rrule=event.rrule, location=event.location)
            for event in self.events()
            for begin, finish in expand(event, start, end)
        ]
        return sorted(shown, key=lambda occurrence: occurrence.start)[:OCCURRENCES_MAX]

    # ------------------------------------------------------------ automations

    def add_automation(self, given: AutomationInput, now: datetime) -> Automation:
        """Store a new automation with its first run planned from `now`."""
        automation = Automation(**given.model_dump(), id=f"au-{secrets.token_hex(6)}")
        automation.nextRunAt = next_run(automation, now, self.event(automation.eventId) if automation.eventId else None)
        self.save_automation(automation)
        return automation

    def update_automation(self, automation_id: str, given: AutomationInput, now: datetime) -> Automation | None:
        """Replace an automation's settings and plan its next run from `now`; None when there is no such automation."""
        current = self.automation(automation_id)
        if current is None:
            return None
        automation = Automation(**given.model_dump(), id=current.id, lastRunAt=current.lastRunAt, lastStatus=current.lastStatus)
        automation.nextRunAt = next_run(automation, now, self.event(automation.eventId) if automation.eventId else None)
        self.save_automation(automation)
        return automation

    def save_automation(self, automation: Automation) -> None:
        """Store an automation as it is."""
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO automations (id, body) VALUES (?, ?)", (automation.id, automation.model_dump_json()))

    def automation(self, automation_id: str) -> Automation | None:
        """The automation `automation_id`, or None."""
        with self.transaction() as db:
            row = db.execute("SELECT body FROM automations WHERE id = ?", (automation_id,)).fetchone()
        return Automation.model_validate_json(row[0]) if row else None

    def automations(self) -> list[Automation]:
        """Every automation, earliest next run first."""
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM automations").fetchall()
        return sorted((Automation.model_validate_json(row[0]) for row in rows), key=lambda a: (a.nextRunAt is None, a.nextRunAt or datetime.max.replace(tzinfo=UTC)))

    def delete_automation(self, automation_id: str) -> bool:
        """Delete an automation; False when there was none."""
        with self.transaction() as db:
            gone = db.execute("DELETE FROM automations WHERE id = ?", (automation_id,)).rowcount > 0
        return gone

    def add_run(self, automation: Automation, at: datetime, status: RunStatus, detail: str = "", run_id: str | None = None, thread_id: str | None = None) -> AutomationRun:
        """Record a run of `automation`, keeping only the last RUNS_KEPT runs."""
        with self.transaction() as db:
            cursor = db.execute("INSERT INTO automation_runs (automation_id, run_id, body) VALUES (?, ?, '{}')", (automation.id, run_id))
            assert cursor.lastrowid is not None
            run = AutomationRun(id=cursor.lastrowid, automationId=automation.id, title=automation.title, at=at, status=status, detail=detail, runId=run_id, threadId=thread_id)
            db.execute("UPDATE automation_runs SET body = ? WHERE id = ?", (run.model_dump_json(), run.id))
            db.execute("DELETE FROM automation_runs WHERE id <= ?", (run.id - RUNS_KEPT,))
        return run

    def runs_after(self, after: int, limit: int = 50) -> list[AutomationRun]:
        """Up to `limit` runs with an id above `after`, oldest first."""
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
        """The id of the newest run (0 when there is none)."""
        with self.transaction() as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) FROM automation_runs").fetchone()
        return int(row[0])

    @property
    def paused(self) -> bool:
        """Whether every automation is paused."""
        with self.transaction() as db:
            row = db.execute("SELECT value FROM calendar_settings WHERE key = 'paused'").fetchone()
        return bool(row and row[0] == "1")

    def set_paused(self, paused: bool) -> None:
        """Pause or resume every automation."""
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
        """Every event as an iCalendar file."""
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
