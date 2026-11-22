"""The calendar's HTTP API: events, automations, their runs and tasks, the pause switch, `.ics` import and export."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel

from symphony_server.calendar_models import Automation, AutomationInput, AutomationRun, CalendarEvent, EventInput, Occurrence, RunStatus, Strict, Trigger
from symphony_server.calendar_store import CalendarStore
from symphony_server.recurrence import next_run
from symphony_server.scheduler import Scheduler


class Tick(BaseModel):
    """One planned run of an automation inside a listed range (the Calendar grid marks them)."""

    automationId: str
    title: str
    at: datetime


class EventList(BaseModel):
    """The occurrences in a listed range, the events they come from, and the automation runs planned in it."""

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
    """Every automation, earliest next run first."""

    automations: list[Automation]


class RunList(BaseModel):
    """Automation runs after a cursor, and the cursor to ask from next."""

    runs: list[AutomationRun]
    last: int


class SchedulerStatus(BaseModel):
    """Whether automations are paused, how many runs are under way, and the next one due."""

    paused: bool
    running: int
    next: Automation | None
    last: int


class PauseBody(Strict):
    """Pause or resume every automation."""

    paused: bool


class Imported(BaseModel):
    """How many events an .ics import added or updated."""

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
    """The agent tasks automations started, for Tasks to label."""

    links: list[TaskLink]


def task_links(store: CalendarStore) -> list[TaskLink]:
    """Each agent task an automation started, with the automation's title, newest first."""
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
        """A 404 naming what was not found."""
        return HTTPException(404, f"no {kind} {item_id!r}")

    @router.get("/calendar/events")
    async def list_events(start: AwareDatetime, end: AwareDatetime) -> EventList:
        """The occurrences of every event between `start` and `end` (at most 400 days), with the planned automation runs."""
        if end <= start or end - start > timedelta(days=400):
            raise HTTPException(400, "pick a range of up to 400 days that ends after it starts")
        occurrences = await asyncio.to_thread(store.occurrences, start, end)
        ids = {occurrence.eventId for occurrence in occurrences}
        planned = await asyncio.to_thread(ticks, store, start, end)
        return EventList(occurrences=occurrences, events=[event for event in store.events() if event.id in ids], ticks=planned)

    @router.post("/calendar/events", status_code=201)
    async def add_event(body: EventInput) -> CalendarEvent:
        """Add an event and replan the automations around events."""
        event = store.add_event(body)
        scheduler.replan()
        return event

    @router.put("/calendar/events/{event_id}")
    async def update_event(event_id: str, body: EventInput) -> CalendarEvent:
        """Change an event and replan the automations around events."""
        event = store.update_event(event_id, body)
        if event is None:
            raise missing("event", event_id)
        scheduler.replan()
        return event

    @router.delete("/calendar/events/{event_id}", status_code=204)
    async def delete_event(event_id: str) -> None:
        """Delete an event and replan the automations around events."""
        if not store.delete_event(event_id):
            raise missing("event", event_id)
        scheduler.replan()

    @router.post("/calendar/import")
    async def import_ics(request: Request) -> Imported:
        """Add or update the events of an .ics file (matched by UID)."""
        data = await request.body()
        try:
            imported = await asyncio.to_thread(store.import_ics, data)
        except ValueError as exc:
            raise HTTPException(400, f"not an .ics calendar: {exc}") from exc
        scheduler.replan()
        return Imported(imported=imported)

    @router.get("/calendar/export.ics")
    async def export_ics() -> Response:
        """Every event as an .ics file."""
        return Response(await asyncio.to_thread(store.export_ics), media_type="text/calendar")

    @router.get("/automations")
    async def list_automations() -> AutomationList:
        """Every automation."""
        return AutomationList(automations=store.automations())

    @router.post("/automations", status_code=201)
    async def add_automation(body: AutomationInput) -> Automation:
        """Add an automation and wake the scheduler for it."""
        if body.eventId is not None and store.event(body.eventId) is None:
            raise missing("event", body.eventId)
        automation = store.add_automation(body, scheduler.clock())
        scheduler.wake()
        return automation

    @router.put("/automations/{automation_id}")
    async def update_automation(automation_id: str, body: AutomationInput) -> Automation:
        """Change an automation (its next run is planned again) and wake the scheduler."""
        automation = store.update_automation(automation_id, body, scheduler.clock())
        if automation is None:
            raise missing("automation", automation_id)
        scheduler.wake()
        return automation

    @router.delete("/automations/{automation_id}", status_code=204)
    async def delete_automation(automation_id: str) -> None:
        """Delete an automation; a run under way finishes, but it never fires again."""
        if not store.delete_automation(automation_id):
            raise missing("automation", automation_id)

    @router.post("/automations/{automation_id}/run")
    async def run_now(automation_id: str) -> AutomationRun:
        """Fire an automation now, outside its schedule."""
        automation = store.automation(automation_id)
        if automation is None:
            raise missing("automation", automation_id)
        run = await scheduler.fire(automation, scheduler.clock())
        automation.lastRunAt, automation.lastStatus = run.at, run.status
        store.save_automation(automation)
        return run

    @router.get("/automations/runs")
    async def runs(after: int = Query(0, ge=0)) -> RunList:
        """Automation runs after the cursor `after`, oldest first."""
        listed = store.runs_after(after)
        return RunList(runs=listed, last=listed[-1].id if listed else max(after, 0))

    @router.get("/automations/tasks")
    async def tasks() -> TaskLinks:
        """The agent tasks automations started, newest first (the Tasks screen links each to its automation and event)."""
        return TaskLinks(links=await asyncio.to_thread(task_links, store))

    @router.get("/automations/status")
    async def status() -> SchedulerStatus:
        """Whether automations are paused, the runs under way and the next automation due."""
        upcoming = [a for a in store.automations() if a.enabled and a.nextRunAt is not None]
        return SchedulerStatus(paused=store.paused, running=scheduler.running, next=upcoming[0] if upcoming else None, last=store.last_run_id())

    @router.post("/automations/pause")
    async def pause(body: PauseBody) -> SchedulerStatus:
        """Pause or resume every automation; resuming plans each one's next run from now."""
        store.set_paused(body.paused)
        if not body.paused:
            scheduler.replan()
        scheduler.wake()
        return await status()

    return router
