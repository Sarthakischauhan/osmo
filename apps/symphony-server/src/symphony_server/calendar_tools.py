"""The calendar tools the voice model and the agent get: `calendar_events`, `calendar_add_event` and `automation_create`."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from core_harness import EventSink, Tool
from pydantic import Field, ValidationError

from symphony_server.calendar_models import Action, AutomationInput, Clock, EventInput, Strict, Trigger, utc_now, zone
from symphony_server.calendar_store import CalendarStore
from symphony_server.canvas import WidgetBoard, show
from symphony_server.scheduler import Scheduler
from symphony_server.widgets import AgendaFooter, AgendaItem, AgendaProps, AgendaWidget


def clock_label(moment: datetime) -> str:
    """`2:00` / `14:05` style: hours and minutes, 12-hour, no AM/PM (the agenda's column)."""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d}"


def agenda(store: CalendarStore, day: date, days: int, tz: ZoneInfo, now: datetime) -> AgendaProps:
    """The agenda widget's props: the occurrences of `days` days from `day`, in `tz`."""
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


class EventsArgs(Strict):
    """calendar_events' arguments."""

    day: date | None = Field(default=None, description="First day to list (YYYY-MM-DD); omit for today.")
    days: int = Field(default=1, ge=1, le=31)
    show: bool = Field(default=False, description="Also show the days as an agenda card on screen.")


class AddEventArgs(Strict):
    """calendar_add_event's arguments."""

    title: str = Field(min_length=1, max_length=200)
    start: datetime = Field(description="Local start time, e.g. 2026-11-22T11:00 (the user's time zone).")
    minutes: int = Field(default=60, ge=5, le=24 * 60, description="How long it lasts.")
    rrule: str | None = Field(default=None, description="An RRULE to repeat it, e.g. FREQ=WEEKLY;BYDAY=SA.")
    location: str | None = Field(default=None, max_length=300)


class CreateAutomationArgs(Strict):
    """automation_create's arguments."""

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
    """A moment in `tz` when the model gave it without a zone."""
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment


class CalendarEventsTool(Tool):
    """List the calendar's events for a day or a few, optionally as an agenda widget."""

    def __init__(self, store: CalendarStore, board: WidgetBoard, timezone: str, clock: Clock = utc_now) -> None:
        super().__init__(
            name="calendar_events",
            description="List the user's local calendar for a day or a few (events with ids, and automations due). show=true also puts an agenda card on screen.",
            parameters=EventsArgs.model_json_schema(),
        )
        self.store, self.board, self.tz, self.clock = store, board, zone(timezone), clock

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        """List the events of the asked days (and show the agenda when asked)."""
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
    """Add an event to the calendar."""

    def __init__(self, store: CalendarStore, scheduler: Scheduler, timezone: str) -> None:
        super().__init__(
            name="calendar_add_event",
            description="Add an event to the user's local calendar (times are in the user's time zone).",
            parameters=AddEventArgs.model_json_schema(),
        )
        self.store, self.scheduler, self.timezone = store, scheduler, timezone

    async def execute(self, *, sink: EventSink, args: dict[str, Any]) -> str:
        """Add the event and replan the automations around events."""
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
    """Create an automation (an agent task, a notification or a note append, at a time or around an event)."""

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
        """Create the automation and wake the scheduler for it."""
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
    """The calendar tools, sharing one store and scheduler."""
    return [
        CalendarEventsTool(store, board, timezone, scheduler.clock),
        CalendarAddEventTool(store, scheduler, timezone),
        AutomationCreateTool(store, scheduler, timezone),
    ]


CALENDAR_TOOL_NAMES = frozenset({"calendar_events", "calendar_add_event", "automation_create"})
