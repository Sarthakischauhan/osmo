"""The calendar in SQLite: events, automations, their runs and the pause switch; `.ics` import and export."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent
from icalendar import vRecur

from symphony_server.calendar_ics import ics_event
from symphony_server.calendar_models import Automation, AutomationInput, AutomationRun, CalendarEvent, EventInput, Occurrence, RunStatus, zone
from symphony_server.recurrence import OCCURRENCES_MAX, expand, next_run
from symphony_server.shared_db import DbClosed, SharedDb

RUNS_KEPT = 500

# Runs a calendar from before agent tasks were linked do not have `run_id`; it is added on open.
TASK_INDEX = "CREATE INDEX IF NOT EXISTS automation_runs_task ON automation_runs (run_id)"
# A scheduled run's claim: the automation and the time it was due. Unique, so one due time never fires twice.
CLAIM_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS automation_runs_claim ON automation_runs (claim)"
# Columns added after the first release, and what they start as on an older calendar.
ADDED_COLUMNS = (("automation_runs", "run_id", "TEXT"), ("automation_runs", "claim", "TEXT"), ("automations", "version", "INTEGER NOT NULL DEFAULT 0"))
SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id TEXT NOT NULL,
    -- The agent run (task) an agent_task automation started, if any.
    run_id TEXT,
    claim TEXT,
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
        self.db = SharedDb(path)
        with self.db.transaction() as db:
            db.executescript(SCHEMA)
            for table, column, kind in ADDED_COLUMNS:
                if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            db.execute(TASK_INDEX)
            db.execute(CLAIM_INDEX)

    def close(self) -> None:
        """Stop an .ics import still running, wait for it (shared_db.STOP_WAIT_S at most), then close the database;
        any call after that raises DbClosed instead of touching it."""
        self.db.close()

    # ------------------------------------------------------------ events

    def add_event(self, given: EventInput, uid: str | None = None) -> CalendarEvent:
        """Store a new event (a new UID unless an import gives one)."""
        event = CalendarEvent(**given.model_dump(), id=f"ev-{secrets.token_hex(6)}", uid=uid or f"{secrets.token_hex(12)}@osmo")
        with self.db.transaction() as db:
            db.execute("INSERT INTO calendar_events (id, uid, body) VALUES (?, ?, ?)", (event.id, event.uid, event.model_dump_json()))
        return event

    def update_event(self, event_id: str, given: EventInput) -> CalendarEvent | None:
        """Replace an event's fields, keeping its id and UID; None when there is no such event."""
        current = self.event(event_id)
        if current is None:
            return None
        event = CalendarEvent(**given.model_dump(), id=current.id, uid=current.uid)
        with self.db.transaction() as db:
            db.execute("UPDATE calendar_events SET body = ? WHERE id = ?", (event.model_dump_json(), event.id))
        return event

    def event(self, event_id: str) -> CalendarEvent | None:
        """The event `event_id`, or None."""
        with self.db.transaction() as db:
            row = db.execute("SELECT body FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        return CalendarEvent.model_validate_json(row[0]) if row else None

    def event_by_uid(self, uid: str) -> CalendarEvent | None:
        """The event with iCalendar UID `uid`, or None."""
        with self.db.transaction() as db:
            row = db.execute("SELECT body FROM calendar_events WHERE uid = ?", (uid,)).fetchone()
        return CalendarEvent.model_validate_json(row[0]) if row else None

    def events(self) -> list[CalendarEvent]:
        """Every event."""
        with self.db.transaction() as db:
            rows = db.execute("SELECT body FROM calendar_events").fetchall()
        return [CalendarEvent.model_validate_json(row[0]) for row in rows]

    def delete_event(self, event_id: str) -> bool:
        """Delete an event; False when there was none."""
        with self.db.transaction() as db:
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
        with self.db.transaction() as db:
            db.execute("INSERT INTO automations (id, body, version) VALUES (?, ?, 0)", (automation.id, automation.model_dump_json()))
        return automation

    def update_automation(self, automation_id: str, given: AutomationInput, now: datetime) -> Automation | None:
        """Replace an automation's settings and plan its next run from `now`; None when there is no such automation.

        The user's edit always wins: it bumps the version, so a run that was starting cannot write its stale copy back."""
        current = self.automation(automation_id)
        if current is None:
            return None
        automation = Automation(**given.model_dump(), id=current.id, lastRunAt=current.lastRunAt, lastStatus=current.lastStatus)
        automation.nextRunAt = next_run(automation, now, self.event(automation.eventId) if automation.eventId else None)
        with self.db.transaction() as db:
            version = self.write(db, automation, None)
        if version is None:
            return None
        return automation.model_copy(update={"version": version})

    @staticmethod
    def write(db: sqlite3.Connection, automation: Automation, expected: int | None) -> int | None:
        """Store `automation` over its row if the row is still at version `expected` (any version when None); the new
        version, or None when the row is gone or has moved on."""
        query = "UPDATE automations SET body = ?, version = version + 1 WHERE id = ?" + ("" if expected is None else " AND version = ?")
        values = (automation.model_dump_json(), automation.id) + (() if expected is None else (expected,))
        if db.execute(query, values).rowcount == 0:
            return None
        row = db.execute("SELECT version FROM automations WHERE id = ?", (automation.id,)).fetchone()
        return int(row[0])

    @staticmethod
    def read(row: tuple[str, int]) -> Automation:
        """An automation from its (body, version) row."""
        return Automation.model_validate_json(row[0]).model_copy(update={"version": row[1]})

    def plan(self, automation: Automation, planned: datetime | None) -> bool:
        """Move the next run of `automation` to `planned`, if nobody changed it since it was read (compare-and-set)."""
        with self.db.transaction() as db:
            return self.write(db, automation.model_copy(update={"nextRunAt": planned}), automation.version) is not None

    def claim(self, automation: Automation, due: datetime, planned: datetime | None, at: datetime, status: RunStatus, detail: str) -> AutomationRun | None:
        """Take the run due at `due` before doing it: in one transaction, move the next run to `planned` (only if the
        automation is unchanged since it was read) and record the run under a claim unique to (automation, due).

        So after a crash or restart the due time is already behind it and never fires twice, and an automation edited,
        disabled or deleted in the meantime is not run. None when the claim was not taken."""
        claimed = automation.model_copy(update={"nextRunAt": planned, "lastRunAt": at, "lastStatus": status})
        try:
            with self.db.transaction() as db:
                if self.write(db, claimed, automation.version) is None:
                    return None
                return self.insert_run(db, automation, at, status, detail, claim=f"{automation.id}@{due.isoformat()}")
        except sqlite3.IntegrityError:
            return None

    def automation(self, automation_id: str) -> Automation | None:
        """The automation `automation_id`, or None."""
        with self.db.transaction() as db:
            row = db.execute("SELECT body, version FROM automations WHERE id = ?", (automation_id,)).fetchone()
        return self.read(row) if row else None

    def automations(self) -> list[Automation]:
        """Every automation, earliest next run first."""
        with self.db.transaction() as db:
            rows = db.execute("SELECT body, version FROM automations").fetchall()
        return sorted((self.read(row) for row in rows), key=lambda a: (a.nextRunAt is None, a.nextRunAt or datetime.max.replace(tzinfo=UTC)))

    def delete_automation(self, automation_id: str) -> bool:
        """Delete an automation; False when there was none. A run under way finishes but writes nothing back to it."""
        with self.db.transaction() as db:
            gone = db.execute("DELETE FROM automations WHERE id = ?", (automation_id,)).rowcount > 0
        return gone

    def add_run(self, automation: Automation, at: datetime, status: RunStatus, detail: str = "") -> AutomationRun:
        """Record a run of `automation` that no schedule claimed (Run now)."""
        with self.db.transaction() as db:
            return self.insert_run(db, automation, at, status, detail, claim=None)

    @staticmethod
    def insert_run(db: sqlite3.Connection, automation: Automation, at: datetime, status: RunStatus, detail: str, claim: str | None) -> AutomationRun:
        """Insert a run row, keeping only the last RUNS_KEPT runs."""
        cursor = db.execute("INSERT INTO automation_runs (automation_id, claim, body) VALUES (?, ?, '{}')", (automation.id, claim))
        assert cursor.lastrowid is not None
        run = AutomationRun(id=cursor.lastrowid, automationId=automation.id, title=automation.title, at=at, status=status, detail=detail)
        db.execute("UPDATE automation_runs SET body = ? WHERE id = ?", (run.model_dump_json(), run.id))
        db.execute("DELETE FROM automation_runs WHERE id <= ?", (run.id - RUNS_KEPT,))
        return run

    def finish_run(self, run: AutomationRun, status: RunStatus, detail: str, run_id: str | None = None, thread_id: str | None = None) -> AutomationRun:
        """Record how a run went (and the agent task it started), and note it on its automation if that still exists."""
        done = run.model_copy(update={"status": status, "detail": detail, "runId": run_id, "threadId": thread_id})
        with self.db.transaction() as db:
            db.execute("UPDATE automation_runs SET body = ?, run_id = ? WHERE id = ?", (done.model_dump_json(), run_id, run.id))
            self.note_last(db, done, latest_only=False)
        return done

    def note_last(self, db: sqlite3.Connection, run: AutomationRun, *, latest_only: bool) -> None:
        """Put `run` on its automation as the last run (only if it already is the last one, when `latest_only`); nothing
        when the automation was deleted: a finished run never brings one back."""
        row = db.execute("SELECT body, version FROM automations WHERE id = ?", (run.automationId,)).fetchone()
        if row is None:
            return
        automation = self.read(row)
        if automation.lastRunAt is not None and (automation.lastRunAt > run.at or (latest_only and automation.lastRunAt != run.at)):
            return
        self.write(db, automation.model_copy(update={"lastRunAt": run.at, "lastStatus": run.status}), automation.version)

    def runs_after(self, after: int, limit: int = 50) -> list[AutomationRun]:
        """Up to `limit` runs with an id above `after`, oldest first."""
        with self.db.transaction() as db:
            rows = db.execute("SELECT body FROM automation_runs WHERE id > ? ORDER BY id LIMIT ?", (after, limit)).fetchall()
        return [AutomationRun.model_validate_json(row[0]) for row in rows]

    def task_runs(self, limit: int = RUNS_KEPT) -> list[AutomationRun]:
        """The latest automation runs that started an agent task, newest first."""
        with self.db.transaction() as db:
            rows = db.execute("SELECT body FROM automation_runs WHERE run_id IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [AutomationRun.model_validate_json(row[0]) for row in rows]

    def started(self) -> list[AutomationRun]:
        """Automation runs whose agent task has not finished yet."""
        return [run for run in self.task_runs() if run.status == "started"]

    def settle(self, task_id: str, status: RunStatus, detail: str) -> AutomationRun | None:
        """Record how the agent task `task_id` ended on its automation run (and on the automation, if it is its
        latest run); None when no automation started that task or it was already settled."""
        with self.db.transaction() as db:
            row = db.execute("SELECT body FROM automation_runs WHERE run_id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            run = AutomationRun.model_validate_json(row[0])
            if run.status != "started":
                return None
            run.status, run.detail = status, detail
            db.execute("UPDATE automation_runs SET body = ? WHERE run_id = ?", (run.model_dump_json(), task_id))
            self.note_last(db, run, latest_only=True)
        return run

    def unlinked(self) -> list[AutomationRun]:
        """Runs still `started` with no agent task: claimed by a server that stopped before the run was done."""
        with self.db.transaction() as db:
            rows = db.execute("SELECT body FROM automation_runs WHERE run_id IS NULL ORDER BY id").fetchall()
        return [run for run in (AutomationRun.model_validate_json(row[0]) for row in rows) if run.status == "started"]

    def last_run_id(self) -> int:
        """The id of the newest run (0 when there is none)."""
        with self.db.transaction() as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) FROM automation_runs").fetchone()
        return int(row[0])

    @property
    def paused(self) -> bool:
        """Whether every automation is paused."""
        with self.db.transaction() as db:
            row = db.execute("SELECT value FROM calendar_settings WHERE key = 'paused'").fetchone()
        return bool(row and row[0] == "1")

    def set_paused(self, paused: bool) -> None:
        """Pause or resume every automation."""
        with self.db.transaction() as db:
            db.execute("INSERT OR REPLACE INTO calendar_settings (key, value) VALUES ('paused', ?)", ("1" if paused else "0",))

    # ------------------------------------------------------------ .ics

    def import_ics(self, data: bytes) -> int:
        """Add (or update, by UID) every VEVENT of an .ics file; how many were taken. Stops early, writing nothing
        more, when the store is closing or closed."""
        taken = 0
        with self.db.job() as stop, suppress(DbClosed):
            for component in ICalendar.from_ical(data).walk("VEVENT"):
                parsed = ics_event(component)
                if stop.is_set():
                    break
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
