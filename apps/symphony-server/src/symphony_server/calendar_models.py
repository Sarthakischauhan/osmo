"""The local calendar's records: events (an IANA zone and an optional RRULE), automations and their runs.

An automation fires on its own schedule (`time`: a moment, optionally repeating by RRULE) or around an event
(`event_start` / `event_end`, plus an offset in minutes), and does one thing: start a background agent run on a new
thread (`agent_task`), raise a desktop notification (`notify`) or append to a vault note (`note_append`).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Trigger = Literal["time", "event_start", "event_end"]
Action = Literal["agent_task", "notify", "note_append"]
RunStatus = Literal["started", "ok", "error", "skipped"]


def zone(name: str) -> ZoneInfo:
    """The ZoneInfo of an IANA zone name; ValueError for a name the system does not know."""
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


class Strict(BaseModel):
    """A request body that refuses fields it does not know."""

    model_config = ConfigDict(extra="forbid")


class EventInput(Strict):
    """An event as the user or a tool gives it: a title, an aware start and end in an IANA zone, an optional RRULE."""

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
    """A stored event: its id, and the iCalendar UID it imports and exports under."""

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


class AutomationInput(Strict):
    """An automation as given: when it fires (`trigger`, and `at`/`rrule` or an event and an offset) and what it does."""

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
    # `note_append`: the vault note to add to (vault_paths checks the path).
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
    """A stored automation, with its next planned run and how its last run went."""

    id: str
    lastRunAt: datetime | None = None
    lastStatus: RunStatus | None = None
    nextRunAt: datetime | None = None


class AutomationRun(BaseModel):
    """One firing of an automation: when, how it went, and the agent task it started, if any."""

    id: int
    automationId: str
    title: str
    at: datetime
    status: RunStatus
    detail: str = ""
    runId: str | None = None
    threadId: str | None = None


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current moment, in UTC (the scheduler's default clock)."""
    return datetime.now(UTC)


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
