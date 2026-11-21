"""The local calendar: recurrence on wall-clock time, the scheduler (fake clock), .ics, tools and the API."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from core_harness import EventSink
from pydantic import ValidationError

from conftest import make_client
from symphony_server.canvas import OSMO_WIDGET, WidgetBoard
from symphony_server.local_calendar import (
    MISSED_GRACE,
    Automation,
    AutomationCreateTool,
    AutomationInput,
    CalendarEventsTool,
    CalendarStore,
    EventInput,
    RunStatus,
    Scheduler,
    expand,
    next_run,
)
from symphony_server.settings import DEFAULT_APPROVAL_TOOLS, Settings

LA = ZoneInfo("America/Los_Angeles")


def la(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=LA)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def weekly_notes() -> EventInput:
    return EventInput(title="Weekly notes", start=la(2026, 11, 21, 14), end=la(2026, 11, 21, 15), timezone="America/Los_Angeles", rrule="FREQ=WEEKLY;BYDAY=SA")


def test_a_weekly_event_keeps_its_wall_clock_time_across_dst() -> None:
    event = EventInput(title="Run", start=la(2026, 10, 24, 9), end=la(2026, 10, 24, 10), timezone="America/Los_Angeles", rrule="FREQ=WEEKLY")
    shown = expand(event, la(2026, 10, 20), la(2026, 11, 10))
    assert [(s.month, s.day, s.hour) for s, _ in shown] == [(10, 24, 9), (10, 31, 9), (11, 7, 9)]
    # DST ended on Nov 1: same 9 AM, a different UTC hour.
    assert shown[0][0].astimezone(UTC).hour == 16 and shown[2][0].astimezone(UTC).hour == 17


def test_events_validate_their_order_zone_and_rule() -> None:
    with pytest.raises(ValidationError, match="end after it starts"):
        EventInput(title="x", start=la(2026, 1, 1, 10), end=la(2026, 1, 1, 9))
    with pytest.raises(ValidationError, match="unknown time zone"):
        EventInput(title="x", start=la(2026, 1, 1, 10), end=la(2026, 1, 1, 11), timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        EventInput(title="x", start=la(2026, 1, 1, 10), end=la(2026, 1, 1, 11), rrule="FREQ=SOMETIMES")


def test_next_run_for_time_and_event_triggers() -> None:
    event = weekly_notes()
    at_end = AutomationInput(title="Summarize", trigger="event_end", eventId="e", action="agent_task", prompt="p")
    assert next_run(at_end, la(2026, 11, 21, 14, 30), event) == la(2026, 11, 21, 15)
    assert next_run(at_end, la(2026, 11, 21, 15), event) == la(2026, 11, 28, 15)
    before = AutomationInput(title="Heads up", trigger="event_start", eventId="e", offsetMinutes=-10, action="notify", prompt="p")
    assert next_run(before, la(2026, 11, 21, 13), event) == la(2026, 11, 21, 13, 50)
    daily = AutomationInput(title="Brief", trigger="time", at=la(2026, 11, 16, 8), rrule="FREQ=DAILY", timezone="America/Los_Angeles", action="agent_task", prompt="p")
    assert next_run(daily, la(2026, 11, 21, 8), None) == la(2026, 11, 22, 8)
    once = AutomationInput(title="Once", trigger="time", at=la(2026, 11, 21, 9), action="notify", prompt="p")
    assert next_run(once, la(2026, 11, 21, 10), None) is None


def test_automation_inputs_need_what_their_trigger_and_action_use() -> None:
    with pytest.raises(ValidationError, match="needs `at`"):
        AutomationInput(title="x", trigger="time", action="notify", prompt="p")
    with pytest.raises(ValidationError, match="needs `eventId`"):
        AutomationInput(title="x", trigger="event_end", action="notify", prompt="p")
    with pytest.raises(ValidationError, match="needs `notePath`"):
        AutomationInput(title="x", trigger="time", at=la(2026, 1, 1), action="note_append", prompt="p")


class Recorder:
    def __init__(self, status: RunStatus = "ok") -> None:
        self.fired: list[str] = []
        self.status = status

    async def __call__(self, automation: Automation) -> tuple[RunStatus, str, str | None, str | None]:
        self.fired.append(automation.title)
        if self.status == "error":
            raise RuntimeError("model offline")
        return self.status, "done", None, None


def scheduled(now: datetime, recorder: Recorder) -> tuple[CalendarStore, Scheduler, Clock]:
    store, clock = CalendarStore(None), Clock(now)
    return store, Scheduler(store, recorder, clock), clock


def test_the_scheduler_runs_what_is_due_once_and_plans_the_next_run() -> None:
    recorder = Recorder()
    store, scheduler, clock = scheduled(la(2026, 11, 21, 7, 59), recorder)
    brief = store.add_automation(AutomationInput(title="Brief", trigger="time", at=la(2026, 11, 21, 8), rrule="FREQ=DAILY", timezone="America/Los_Angeles", action="notify", prompt="Good morning"), clock())
    assert asyncio.run(scheduler.tick()) == [] and recorder.fired == []
    clock.now = la(2026, 11, 21, 8, 0, 5)
    [run] = asyncio.run(scheduler.tick())
    assert (run.title, run.status) == ("Brief", "ok")
    assert asyncio.run(scheduler.tick()) == []
    saved = store.automation(brief.id)
    assert saved is not None and saved.nextRunAt == la(2026, 11, 22, 8) and saved.lastStatus == "ok"
    assert recorder.fired == ["Brief"]


def test_a_run_missed_while_osmo_was_closed_is_skipped_not_run_late() -> None:
    recorder = Recorder()
    store, scheduler, clock = scheduled(la(2026, 11, 21, 7), recorder)
    store.add_automation(AutomationInput(title="Brief", trigger="time", at=la(2026, 11, 21, 8), rrule="FREQ=DAILY", timezone="America/Los_Angeles", action="notify", prompt="p"), clock())
    clock.now = la(2026, 11, 21, 8) + MISSED_GRACE + timedelta(seconds=1)
    [run] = asyncio.run(scheduler.tick())
    assert run.status == "skipped" and "missed" in run.detail
    assert recorder.fired == []


def test_paused_automations_do_not_run_and_owe_nothing_on_resume() -> None:
    recorder = Recorder()
    store, scheduler, clock = scheduled(la(2026, 11, 21, 7), recorder)
    brief = store.add_automation(AutomationInput(title="Brief", trigger="time", at=la(2026, 11, 21, 8), rrule="FREQ=DAILY", timezone="America/Los_Angeles", action="notify", prompt="p"), clock())
    store.set_paused(True)
    clock.now = la(2026, 11, 21, 8, 1)
    assert asyncio.run(scheduler.tick()) == []
    store.set_paused(False)
    saved = store.automation(brief.id)
    assert saved is not None and saved.nextRunAt == la(2026, 11, 22, 8)
    assert recorder.fired == []


def test_a_failing_automation_is_recorded_and_the_rest_still_run() -> None:
    store, scheduler, clock = scheduled(la(2026, 11, 21, 7), Recorder("error"))
    store.add_automation(AutomationInput(title="A", trigger="time", at=la(2026, 11, 21, 8), action="notify", prompt="p"), clock())
    store.add_automation(AutomationInput(title="B", trigger="time", at=la(2026, 11, 21, 8), action="notify", prompt="p"), clock())
    clock.now = la(2026, 11, 21, 8, 0, 1)
    runs = asyncio.run(scheduler.tick())
    assert [(r.title, r.status, r.detail) for r in sorted(runs, key=lambda r: r.title)] == [("A", "error", "model offline"), ("B", "error", "model offline")]
    assert [r.id for r in store.runs_after(0)] == [run.id for run in runs]


def test_the_scheduler_sleeps_until_the_next_run_capped_at_a_minute() -> None:
    store, scheduler, clock = scheduled(la(2026, 11, 21, 7, 59, 50), Recorder())
    assert scheduler.seconds_to_next() == 60.0
    store.add_automation(AutomationInput(title="Brief", trigger="time", at=la(2026, 11, 21, 8), action="notify", prompt="p"), clock())
    assert scheduler.seconds_to_next() == 10.0


def test_ics_round_trips_by_uid(tmp_path: Path) -> None:
    store = CalendarStore(tmp_path / "osmo.db")
    store.add_event(weekly_notes())
    exported = store.export_ics()
    assert b"RRULE:FREQ=WEEKLY;BYDAY=SA" in exported and b"TZID=America/Los_Angeles" in exported
    assert store.import_ics(exported) == 1
    assert len(store.events()) == 1
    other = CalendarStore(None)
    assert other.import_ics(exported) == 1
    [event] = other.events()
    assert (event.title, event.rrule, event.timezone) == ("Weekly notes", "FREQ=WEEKLY;BYDAY=SA", "America/Los_Angeles")


def test_the_events_tool_lists_and_shows_an_agenda() -> None:
    store, clock = CalendarStore(None), Clock(la(2026, 11, 21, 14, 30))
    store.add_event(EventInput(title="Farmers market", start=la(2026, 11, 21, 9), end=la(2026, 11, 21, 10, 30), timezone="America/Los_Angeles"))
    store.add_event(weekly_notes())
    store.add_event(EventInput(title="Dinner, Mission", start=la(2026, 11, 21, 19), end=la(2026, 11, 21, 21), timezone="America/Los_Angeles"))
    sink = EventSink()
    tool = CalendarEventsTool(store, WidgetBoard(), "America/Los_Angeles", clock)
    result = json.loads(asyncio.run(tool.execute(sink=sink, args={"show": True})))
    assert [e["title"] for e in result["events"]] == ["Farmers market", "Weekly notes", "Dinner, Mission"]
    [shown] = [e.payload["widget"] for e in sink.events if e.event_type == OSMO_WIDGET]
    assert shown["type"] == "agenda" and shown["props"]["title"] == "Today"
    assert [(i["time"], i["state"]) for i in shown["props"]["items"]] == [("9:00", "past"), ("2:00", "now"), ("7:00", "upcoming")]


def test_automation_create_reads_local_times_and_waits_for_approval_by_default() -> None:
    assert "automation_create" in DEFAULT_APPROVAL_TOOLS and "calendar_add_event" not in DEFAULT_APPROVAL_TOOLS
    store = CalendarStore(None)
    scheduler = Scheduler(store, Recorder(), Clock(la(2026, 11, 21, 7)))
    tool = AutomationCreateTool(store, scheduler, "America/Los_Angeles")
    made = json.loads(asyncio.run(tool.execute(sink=EventSink(), args={"title": "Brief", "trigger": "time", "at": "2026-11-22T08:00", "rrule": "FREQ=DAILY", "action": "agent_task", "prompt": "Brief me"})))
    assert made["next"] == "2026-11-22T08:00:00-08:00"
    refused = json.loads(asyncio.run(tool.execute(sink=EventSink(), args={"title": "x", "trigger": "event_end", "eventId": "ev-nope", "action": "notify", "prompt": "p"})))
    assert "no event 'ev-nope'" in refused["error"]


def test_the_api_lists_occurrences_runs_now_pauses_and_reports_status(tmp_path: Path) -> None:
    with make_client(Settings(database_file=tmp_path / "osmo.db", timezone="America/Los_Angeles")) as client:
        event = client.post("/api/calendar/events", json=json.loads(weekly_notes().model_dump_json())).json()
        listed = client.get("/api/calendar/events", params={"start": "2026-11-16T00:00:00-08:00", "end": "2026-12-01T00:00:00-08:00"}).json()
        assert [o["start"] for o in listed["occurrences"]] == ["2026-11-21T14:00:00-08:00", "2026-11-28T14:00:00-08:00"]
        brief = client.post("/api/automations", json={"title": "Brief", "trigger": "time", "at": "2026-11-16T08:00:00-08:00", "rrule": "FREQ=DAILY", "timezone": "America/Los_Angeles", "action": "notify", "prompt": "p"})
        assert brief.status_code == 201
        week = client.get("/api/calendar/events", params={"start": "2026-11-16T00:00:00-08:00", "end": "2026-11-23T00:00:00-08:00"}).json()
        assert [t["at"] for t in week["ticks"] if t["title"] == "Brief"][:2] == ["2026-11-16T08:00:00-08:00", "2026-11-17T08:00:00-08:00"]
        assert len([t for t in week["ticks"] if t["title"] == "Brief"]) == 7
        client.delete(f"/api/automations/{brief.json()['id']}")
        made = client.post("/api/automations", json={"title": "Remind", "trigger": "event_start", "eventId": event["id"], "offsetMinutes": -5, "action": "notify", "prompt": "Weekly notes in 5"})
        assert made.status_code == 201
        ran = client.post(f"/api/automations/{made.json()['id']}/run").json()
        assert (ran["status"], ran["detail"]) == ("ok", "Weekly notes in 5")
        runs = client.get("/api/automations/runs", params={"after": 0}).json()
        assert [r["title"] for r in runs["runs"]] == ["Remind"] and runs["last"] == ran["id"]
        assert client.get("/api/automations/runs", params={"after": runs["last"]}).json()["runs"] == []
        assert client.post("/api/automations/pause", json={"paused": True}).json()["paused"] is True
        status = client.get("/api/automations/status").json()
        assert status["paused"] is True and status["next"]["title"] == "Remind"
        assert client.post("/api/automations", json={"title": "x", "trigger": "event_end", "eventId": "ev-nope", "action": "notify", "prompt": "p"}).status_code == 404
        ics = client.get("/api/calendar/export.ics")
        assert ics.headers["content-type"].startswith("text/calendar") and b"Weekly notes" in ics.content
