"""When events happen and automations fire: RRULEs expanded on wall-clock time, so a weekly 2 PM stays 2 PM across DST."""

from __future__ import annotations

from datetime import datetime, timedelta

from dateutil.rrule import rrule, rrulestr

from symphony_server.calendar_models import AutomationInput, EventInput, zone

# The most occurrences one listing expands (a minutely RRULE over a year would be ~500k).
OCCURRENCES_MAX = 2000


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
