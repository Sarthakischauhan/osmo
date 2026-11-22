import { ChevronLeft, ChevronRight } from "lucide-react";
import { type FormEvent, type MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type Automation,
  type AutomationInput,
  type CalendarApi,
  type CalendarEvent,
  GRID_FIRST_HOUR,
  GRID_LAST_HOUR,
  HOUR_PX,
  type Occurrence,
  type TaskLink,
  type Tick,
  addDays,
  calendarApi,
  isoWithOffset,
  placement,
  rangeLabel,
  sameDay,
  spanLabel,
  triggerLabel,
  weekStart,
} from "@/lib/calendar";

export interface CalendarViewProps {
  api?: CalendarApi;
  /** "Now" for the grid (screenshots and tests); the clock otherwise. */
  now?: Date;
  /** An automation to show first (Tasks opens the calendar on the automation that started a task). */
  focus?: string;
  /** Open the conversation of a task an automation started. */
  onOpenTask?: (threadId: string) => void;
}

type Selection =
  | { kind: "event"; occurrence: Occurrence }
  | { kind: "automation"; id: string }
  | { kind: "new-event"; at: Date }
  | { kind: "new-automation"; at: Date; eventId?: string };

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const GRID_HOURS = GRID_LAST_HOUR - GRID_FIRST_HOUR;
/** Clicks on the grid snap to half hours. */
const SLOT_MINUTES = 30;
const zoneName = Intl.DateTimeFormat().resolvedOptions().timeZone;
const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-US", { weekday: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";

function hourLabel(hour: number): string {
  if (hour === GRID_FIRST_HOUR) return "8 AM";
  if (hour === 12) return "12 PM";
  return String(hour % 12 || 12);
}

const clock = (date: Date) => date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

/** The start of the next whole hour, for the toolbar's New buttons. */
const nextHour = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours() + 1);

/** The week grid from GUI's calendar-week mockup, with the selected event's or automation's detail on the right. */
export function CalendarView({ api: given, now: fixedNow, focus, onOpenTask }: CalendarViewProps) {
  const api = useMemo(() => given ?? calendarApi(), [given]);
  const [now, setNow] = useState(() => fixedNow ?? new Date());
  const [start, setStart] = useState(() => weekStart(fixedNow ?? new Date()));
  const [occurrences, setOccurrences] = useState<Occurrence[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [tasks, setTasks] = useState<TaskLink[]>([]);
  const [selected, setSelected] = useState<Selection | undefined>(() => (focus ? { kind: "automation", id: focus } : undefined));
  useEffect(() => {
    if (focus) setSelected({ kind: "automation", id: focus });
  }, [focus]);
  const [error, setError] = useState<string>();
  // The grid stretches to the window; hours are at least HOUR_PX tall and grow with the space.
  const bodyRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const [hourPx, setHourPx] = useState(HOUR_PX);
  const [hover, setHover] = useState<{ day: number; at: Date }>();

  useEffect(() => {
    const body = bodyRef.current;
    if (!body || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setHourPx(Math.max(HOUR_PX, body.clientHeight / GRID_HOURS)));
    observer.observe(body);
    return () => observer.disconnect();
  }, []);

  // Stacked under the grid on narrow windows, the card may be off screen when a slot is picked.
  useEffect(() => {
    if (selected) detailRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  useEffect(() => {
    if (fixedNow) return;
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, [fixedNow]);

  const load = useCallback(async () => {
    try {
      const [week, listed, linked] = await Promise.all([api.week(start, addDays(start, 7)), api.automations(), api.tasks()]);
      setOccurrences(week.occurrences);
      setEvents(week.events);
      setTicks(week.ticks);
      setAutomations(listed);
      setTasks(linked);
      setError(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [api, start]);

  useEffect(() => void load(), [load]);

  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const scale = hourPx / HOUR_PX;
  const gridHeight = GRID_HOURS * hourPx;
  const place = (from: Date, to: Date) => {
    const { top, height } = placement(from, to);
    return { top: top * scale, height: height * scale };
  };
  const hourLabels = Array.from({ length: GRID_HOURS + 1 }, (_, i) => GRID_FIRST_HOUR + i).filter((hour) => hourPx >= 44 || hour % 2 === 0);

  /** The half-hour slot under the pointer in a day column. */
  const slotAt = (event: MouseEvent<HTMLElement>, day: Date): Date => {
    const y = event.clientY - event.currentTarget.getBoundingClientRect().top;
    const minutes = Math.floor((y / hourPx) * (60 / SLOT_MINUTES)) * SLOT_MINUTES;
    const clamped = Math.min(Math.max(minutes, 0), GRID_HOURS * 60 - SLOT_MINUTES);
    return new Date(day.getFullYear(), day.getMonth(), day.getDate(), GRID_FIRST_HOUR, clamped);
  };
  const onEmpty = (event: MouseEvent<HTMLElement>) => event.target === event.currentTarget;
  const draft = selected?.kind === "new-event" || (selected?.kind === "new-automation" && !selected.eventId) ? selected : undefined;
  const linked = (eventId: string) => automations.filter((automation) => automation.eventId === eventId);
  const automation = selected?.kind === "automation" ? automations.find((a) => a.id === selected.id) : undefined;
  const eventAutomation = selected?.kind === "event" ? linked(selected.occurrence.eventId)[0] : undefined;
  const shownAutomation = automation ?? eventAutomation;

  const act = async (work: () => Promise<unknown>) => {
    try {
      await work();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const toInput = (a: Automation): AutomationInput => ({
    title: a.title, trigger: a.trigger, at: a.at, rrule: a.rrule, timezone: a.timezone, eventId: a.eventId,
    offsetMinutes: a.offsetMinutes, action: a.action, prompt: a.prompt, notePath: a.notePath, enabled: a.enabled,
  });

  return (
    <div className="osmo-calendar">
      <header className="osmo-view-head">
        <h1>Calendar</h1>
        <div className="osmo-view-tools">
          <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={() => setSelected({ kind: "new-event", at: nextHour(now) })}>New event</button>
          <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={() => setSelected({ kind: "new-automation", at: nextHour(now) })}>New automation</button>
        </div>
      </header>
      {error && <p className="osmo-notes-error" role="alert">{error}</p>}
      <div className="osmo-calendar-pane">
        <div className="osmo-week">
          <div className="osmo-week-tools">
            <button type="button" className="osmo-week-nav" aria-label="Previous week" onClick={() => setStart(addDays(start, -7))}>
              <ChevronLeft size={14} />
            </button>
            <span className="osmo-week-range">{rangeLabel(start)}</span>
            <button type="button" className="osmo-week-nav" aria-label="Next week" onClick={() => setStart(addDays(start, 7))}>
              <ChevronRight size={14} />
            </button>
            <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={() => setStart(weekStart(now))}>Today</button>
          </div>
          <div className="osmo-week-head">
            <div />
            {days.map((day, i) => (
              <div key={i} className="osmo-dow" data-today={sameDay(day, now)}>
                <span className="osmo-dow-name">{DAY_NAMES[i]}</span>
                <span className="osmo-dow-num">{day.getDate()}</span>
              </div>
            ))}
          </div>
          <div ref={bodyRef} className="osmo-week-body" style={{ minHeight: GRID_HOURS * HOUR_PX }}>
            <div className="osmo-week-hours">
              {hourLabels.map((hour) => (
                <span key={hour} style={{ top: (hour - GRID_FIRST_HOUR) * hourPx }}>{hourLabel(hour)}</span>
              ))}
            </div>
            {days.some((day) => sameDay(day, now)) && now.getHours() >= GRID_FIRST_HOUR && now.getHours() < GRID_LAST_HOUR && (
              <div className="osmo-week-now" style={{ top: place(now, now).top }} />
            )}
            <div className="osmo-week-lines" aria-hidden>
              {Array.from({ length: GRID_HOURS * 2 + 1 }, (_, i) => <i key={i} data-half={i % 2 === 1} style={{ top: (i * hourPx) / 2 }} />)}
            </div>
            {days.map((day, i) => (
              <div
                key={i}
                className="osmo-week-col"
                data-today={sameDay(day, now)}
                title="Click to add an event or automation"
                onMouseMove={(event) => {
                  if (!onEmpty(event)) return setHover(undefined);
                  const at = slotAt(event, day);
                  if (hover?.day !== i || hover.at.getTime() !== at.getTime()) setHover({ day: i, at });
                }}
                onMouseLeave={() => setHover(undefined)}
                onClick={(event) => {
                  if (!onEmpty(event)) return;
                  const at = slotAt(event, day);
                  setSelected(selected?.kind === "new-automation" && !selected.eventId ? { kind: "new-automation", at } : { kind: "new-event", at });
                }}
              >
                {hover?.day === i && !(draft && sameDay(draft.at, day) && draft.at.getTime() === hover.at.getTime()) && (
                  <div className="osmo-slot-hover" aria-hidden style={place(hover.at, new Date(hover.at.getTime() + SLOT_MINUTES * 60_000))}>
                    + {clock(hover.at)}
                  </div>
                )}
                {draft && sameDay(draft.at, day) && (
                  <div className="osmo-event" data-draft="true" aria-hidden style={place(draft.at, new Date(draft.at.getTime() + (draft.kind === "new-event" ? 60 : SLOT_MINUTES) * 60_000))}>
                    {draft.kind === "new-event" ? "New event" : "New automation"}
                    <span className="osmo-event-when">{clock(draft.at)}</span>
                  </div>
                )}
                {ticks
                  .filter((tick) => sameDay(new Date(tick.at), day) && automations.find((a) => a.id === tick.automationId)?.trigger === "time")
                  .map((tick) => (
                    <button
                      key={`${tick.automationId}-${tick.at}`}
                      type="button"
                      className="osmo-auto-tick"
                      style={{ top: Math.min(place(new Date(tick.at), new Date(tick.at)).top, gridHeight - 14) }}
                      onClick={() => setSelected({ kind: "automation", id: tick.automationId })}
                    >
                      <i aria-hidden /> {tick.title}
                    </button>
                  ))}
                {occurrences
                  .filter((occurrence) => sameDay(new Date(occurrence.start), day))
                  .map((occurrence) => {
                    const begin = new Date(occurrence.start);
                    const finish = new Date(occurrence.end);
                    const { top, height } = place(begin, finish);
                    const isSelected = selected?.kind === "event" && selected.occurrence.eventId === occurrence.eventId && selected.occurrence.start === occurrence.start;
                    return (
                      <button
                        key={`${occurrence.eventId}-${occurrence.start}`}
                        type="button"
                        className="osmo-event"
                        data-past={finish <= now}
                        data-selected={isSelected}
                        style={{ top, height }}
                        onClick={() => setSelected({ kind: "event", occurrence })}
                      >
                        {occurrence.title}
                        {height >= 30 && <span className="osmo-event-when">{spanLabel(begin, finish)}</span>}
                        {linked(occurrence.eventId).length > 0 && <span className="osmo-event-mark" title="Automation" />}
                      </button>
                    );
                  })}
              </div>
            ))}
          </div>
        </div>
        <aside ref={detailRef} className="osmo-calendar-detail">
          {selected === undefined && (
            <div className="osmo-calendar-empty">
              <h2>Nothing selected</h2>
              <p>Click a time on the grid to add an event or an automation there, or pick one to see its details.</p>
            </div>
          )}
          {selected?.kind === "event" && (
            <EventDetail occurrence={selected.occurrence} event={events.find((e) => e.id === selected.occurrence.eventId)} />
          )}
          {shownAutomation && (selected?.kind === "event" || selected?.kind === "automation") && (
            <AutomationDetail
              automation={shownAutomation}
              withTitle={selected.kind === "automation"}
              tasks={tasks.filter((task) => task.automationId === shownAutomation.id).slice(0, 3)}
              onOpenTask={onOpenTask}
              onRunNow={() => void act(() => api.runNow(shownAutomation.id))}
              onToggle={() => void act(() => api.updateAutomation(shownAutomation.id, { ...toInput(shownAutomation), enabled: !shownAutomation.enabled }))}
            />
          )}
          {selected?.kind === "event" && eventAutomation === undefined && (
            <div className="osmo-calendar-actions">
              <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={() => setSelected({ kind: "new-automation", at: new Date(selected.occurrence.start), eventId: selected.occurrence.eventId })}>
                Add automation
              </button>
            </div>
          )}
          {draft && (
            <div className="osmo-segmented" role="group" aria-label="Create">
              <button type="button" aria-pressed={draft.kind === "new-event"} onClick={() => setSelected({ kind: "new-event", at: draft.at })}>Event</button>
              <button type="button" aria-pressed={draft.kind === "new-automation"} onClick={() => setSelected({ kind: "new-automation", at: draft.at })}>Automation</button>
            </div>
          )}
          {selected?.kind === "new-event" && (
            <NewEvent key={selected.at.getTime()} at={selected.at} onCancel={() => setSelected(undefined)} onCreate={(input) => act(() => api.addEvent(input)).then(() => setSelected(undefined))} />
          )}
          {selected?.kind === "new-automation" && (
            <NewAutomation
              key={`${selected.eventId ?? ""}-${selected.at.getTime()}`}
              eventId={selected.eventId}
              at={selected.at}
              onCancel={() => setSelected(undefined)}
              onCreate={(input) => act(() => api.addAutomation(input)).then(() => setSelected(undefined))}
            />
          )}
          <p className="osmo-tray-note">Automations run from the tray, also with the window closed; agent tasks show in Tasks. Osmo never starts at login.</p>
        </aside>
      </div>
    </div>
  );
}

function EventDetail({ occurrence, event }: { occurrence: Occurrence; event: CalendarEvent | undefined }) {
  const begin = new Date(occurrence.start);
  const finish = new Date(occurrence.end);
  return (
    <>
      <h2>{occurrence.title}</h2>
      <p className="osmo-detail-meta">
        {begin.toLocaleDateString("en-US", { weekday: "short", day: "numeric" })} · {spanLabel(begin, finish)} {finish.getHours() >= 12 ? "PM" : "AM"}
        {occurrence.rrule && <><br /><span className="mono">{occurrence.rrule}</span></>}
        {occurrence.location && <><br />{occurrence.location}</>}
      </p>
      {event?.notes && <p className="osmo-detail-notes">{event.notes}</p>}
    </>
  );
}

const TASK_STATUS: Record<TaskLink["status"], string> = { started: "working", ok: "ok", error: "error", skipped: "skipped" };
const tone = (status: TaskLink["status"]) => (status === "error" ? "red" : status === "skipped" ? "amber" : status === "started" ? "gray" : "green");

function AutomationDetail({ automation, withTitle, tasks, onOpenTask, onRunNow, onToggle }: {
  automation: Automation;
  withTitle: boolean;
  tasks: TaskLink[];
  onOpenTask?: (threadId: string) => void;
  onRunNow: () => void;
  onToggle: () => void;
}) {
  return (
    <>
      {withTitle && (
        <>
          <h2>{automation.title}</h2>
          <p className="osmo-detail-meta">{automation.rrule ? <span className="mono">{automation.rrule}</span> : when(automation.at)}</p>
        </>
      )}
      <section className="osmo-detail-section">
        <div className="osmo-detail-label">Automation</div>
        <dl className="osmo-kv">
          <dt>Trigger</dt><dd>{triggerLabel(automation)}</dd>
          <dt>Action</dt><dd className="mono">{automation.action}</dd>
          {automation.notePath && <><dt>Note</dt><dd className="mono">{automation.notePath}</dd></>}
        </dl>
      </section>
      <section className="osmo-detail-section">
        <div className="osmo-detail-label">{automation.action === "notify" ? "Message" : "Prompt"}</div>
        <pre className="osmo-detail-prompt">{automation.prompt}</pre>
      </section>
      <section className="osmo-detail-section">
        <dl className="osmo-kv">
          <dt>Last run</dt>
          <dd>
            {automation.lastStatus && <span className="osmo-badge" data-tone={automation.lastStatus === "error" ? "red" : automation.lastStatus === "skipped" ? "amber" : "green"}>{automation.lastStatus}</span>}{" "}
            <span className="mono">{when(automation.lastRunAt)}</span>
          </dd>
          <dt>Next run</dt><dd className="mono">{automation.enabled ? when(automation.nextRunAt) : "Paused"}</dd>
        </dl>
      </section>
      {tasks.length > 0 && (
        <section className="osmo-detail-section">
          <div className="osmo-detail-label">Tasks</div>
          <ul className="osmo-detail-tasks">
            {tasks.map((task) => (
              <li key={task.runId}>
                <span className="osmo-badge" data-tone={tone(task.status)}>{TASK_STATUS[task.status]}</span>
                <span className="mono">{when(task.at)}</span>
                {task.threadId && onOpenTask && (
                  <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={() => task.threadId && onOpenTask(task.threadId)}>Open</button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="osmo-calendar-actions">
        <button type="button" className="osmo-btn osmo-btn-primary osmo-btn-sm" onClick={onRunNow}>Run now</button>
        <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={onToggle}>{automation.enabled ? "Pause" : "Resume"}</button>
      </div>
    </>
  );
}

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function localInput(date: Date): string {
  return isoWithOffset(date).slice(0, 16);
}

function NewEvent({ at, onCreate, onCancel }: { at: Date; onCreate: (input: Omit<CalendarEvent, "id" | "uid">) => Promise<void>; onCancel: () => void }) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const begin = new Date(field(form, "start"));
    const minutes = Number(field(form, "minutes")) || 60;
    void onCreate({
      title: field(form, "title"),
      start: isoWithOffset(begin),
      end: isoWithOffset(new Date(begin.getTime() + minutes * 60_000)),
      timezone: zoneName,
      rrule: field(form, "rrule") || null,
      location: null,
      notes: null,
    });
  };
  return (
    <form className="osmo-calendar-form" onSubmit={submit}>
      <h2>New event</h2>
      <label>Title<input name="title" required autoFocus /></label>
      <label>Starts<input name="start" type="datetime-local" required defaultValue={localInput(at)} /></label>
      <label>Minutes<input name="minutes" type="number" min={5} defaultValue={60} /></label>
      <label>Repeats (RRULE)<input name="rrule" placeholder="FREQ=WEEKLY;BYDAY=SA" /></label>
      <FormActions submit="Add event" onCancel={onCancel} />
    </form>
  );
}

function NewAutomation({ eventId, at, onCreate, onCancel }: { eventId: string | undefined; at: Date; onCreate: (input: AutomationInput) => Promise<void>; onCancel: () => void }) {
  const [action, setAction] = useState<AutomationInput["action"]>("agent_task");
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const trigger = eventId ? (field(form, "trigger") === "event_start" ? "event_start" : "event_end") : "time";
    void onCreate({
      title: field(form, "title"),
      trigger,
      at: trigger === "time" ? isoWithOffset(new Date(field(form, "at"))) : null,
      rrule: trigger === "time" ? field(form, "rrule") || null : null,
      timezone: zoneName,
      eventId: eventId ?? null,
      offsetMinutes: Number(field(form, "offset")) || 0,
      action,
      prompt: field(form, "prompt"),
      notePath: action === "note_append" ? field(form, "note") : null,
      enabled: true,
    });
  };
  return (
    <form className="osmo-calendar-form" onSubmit={submit}>
      <h2>New automation</h2>
      <label>Title<input name="title" required autoFocus /></label>
      {eventId ? (
        <>
          <label>When<select name="trigger" defaultValue="event_end"><option value="event_start">At event start</option><option value="event_end">At event end</option></select></label>
          <label>Offset (minutes)<input name="offset" type="number" defaultValue={0} /></label>
        </>
      ) : (
        <>
          <label>First run<input name="at" type="datetime-local" required defaultValue={localInput(at)} /></label>
          <label>Repeats (RRULE)<input name="rrule" placeholder="FREQ=DAILY" /></label>
        </>
      )}
      <label>Action
        <select name="action" value={action} onChange={(event) => setAction(event.target.value === "notify" ? "notify" : event.target.value === "note_append" ? "note_append" : "agent_task")}>
          <option value="agent_task">Agent task</option>
          <option value="notify">Notification</option>
          <option value="note_append">Append to a note</option>
        </select>
      </label>
      {action === "note_append" && <label>Note<input name="note" required placeholder="Journal/2026.md" /></label>}
      <label>{action === "notify" ? "Message" : "Prompt"}<textarea name="prompt" required rows={4} /></label>
      <FormActions submit="Create automation" onCancel={onCancel} />
    </form>
  );
}

function FormActions({ submit, onCancel }: { submit: string; onCancel: () => void }) {
  return (
    <div className="osmo-calendar-actions">
      <button type="submit" className="osmo-btn osmo-btn-primary osmo-btn-sm">{submit}</button>
      <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={onCancel}>Cancel</button>
    </div>
  );
}
