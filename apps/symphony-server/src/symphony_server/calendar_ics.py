"""Reading `.ics` VEVENTs (icalendar) into calendar events."""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from symphony_server.calendar_models import EventInput


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
