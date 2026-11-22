import { z } from "zod";
import type { Fetcher } from "./usage";

/** The Calendar screen's side of `/api/calendar` and `/api/automations` (symphony_server/local_calendar.py). */
export const occurrenceSchema = z.object({
  eventId: z.string(),
  title: z.string(),
  start: z.string(),
  end: z.string(),
  rrule: z.string().nullish(),
  location: z.string().nullish(),
});
export const calendarEventSchema = z.object({
  id: z.string(),
  uid: z.string(),
  title: z.string(),
  start: z.string(),
  end: z.string(),
  timezone: z.string(),
  rrule: z.string().nullish(),
  location: z.string().nullish(),
  notes: z.string().nullish(),
});
export const automationSchema = z.object({
  id: z.string(),
  title: z.string(),
  trigger: z.enum(["time", "event_start", "event_end"]),
  at: z.string().nullish(),
  rrule: z.string().nullish(),
  timezone: z.string(),
  eventId: z.string().nullish(),
  offsetMinutes: z.number(),
  action: z.enum(["agent_task", "notify", "note_append"]),
  prompt: z.string(),
  notePath: z.string().nullish(),
  enabled: z.boolean(),
  lastRunAt: z.string().nullish(),
  lastStatus: z.enum(["started", "ok", "error", "skipped"]).nullish(),
  nextRunAt: z.string().nullish(),
});
export const automationRunSchema = z.object({
  id: z.number(),
  automationId: z.string(),
  title: z.string(),
  at: z.string(),
  status: z.enum(["started", "ok", "error", "skipped"]),
  detail: z.string(),
  runId: z.string().nullish(),
  threadId: z.string().nullish(),
});
/** An agent task an automation started (`GET /api/automations/tasks`): Tasks and the calendar link each other by it. */
export const taskLinkSchema = z.object({
  runId: z.string(),
  threadId: z.string().nullish(),
  automationId: z.string(),
  title: z.string(),
  at: z.string(),
  status: z.enum(["started", "ok", "error", "skipped"]),
  trigger: z.enum(["time", "event_start", "event_end"]).nullish(),
  eventId: z.string().nullish(),
  eventTitle: z.string().nullish(),
});
export const tickSchema = z.object({ automationId: z.string(), title: z.string(), at: z.string() });
export const schedulerStatusSchema = z.object({ paused: z.boolean(), running: z.number(), next: automationSchema.nullish(), last: z.number() });

export type Occurrence = z.infer<typeof occurrenceSchema>;
export type CalendarEvent = z.infer<typeof calendarEventSchema>;
export type Automation = z.infer<typeof automationSchema>;
export type AutomationRun = z.infer<typeof automationRunSchema>;
export type SchedulerStatus = z.infer<typeof schedulerStatusSchema>;
export type Tick = z.infer<typeof tickSchema>;
export type TaskLink = z.infer<typeof taskLinkSchema>;
export type AutomationInput = Omit<Automation, "id" | "lastRunAt" | "lastStatus" | "nextRunAt">;

const browserFetch: Fetcher = (input, init) => fetch(input, init);
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

async function read<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined);
    const detail = z.object({ detail: z.unknown() }).safeParse(body);
    throw new Error(detail.success ? (typeof detail.data.detail === "string" ? detail.data.detail : "That was not accepted") : `HTTP ${response.status}`);
  }
  return schema.parse(await response.json());
}

export function calendarApi(fetcher: Fetcher = browserFetch) {
  return {
    async week(start: Date, end: Date): Promise<{ occurrences: Occurrence[]; events: CalendarEvent[]; ticks: Tick[] }> {
      const query = new URLSearchParams({ start: isoWithOffset(start), end: isoWithOffset(end) });
      const schema = z.object({ occurrences: z.array(occurrenceSchema), events: z.array(calendarEventSchema), ticks: z.array(tickSchema) });
      return read(await fetcher(`/api/calendar/events?${query}`), schema);
    },
    async addEvent(event: Omit<CalendarEvent, "id" | "uid">): Promise<CalendarEvent> {
      return read(await fetcher("/api/calendar/events", json("POST", event)), calendarEventSchema);
    },
    async automations(): Promise<Automation[]> {
      return (await read(await fetcher("/api/automations"), z.object({ automations: z.array(automationSchema) }))).automations;
    },
    async addAutomation(input: AutomationInput): Promise<Automation> {
      return read(await fetcher("/api/automations", json("POST", input)), automationSchema);
    },
    async updateAutomation(id: string, input: AutomationInput): Promise<Automation> {
      return read(await fetcher(`/api/automations/${encodeURIComponent(id)}`, json("PUT", input)), automationSchema);
    },
    async runNow(id: string): Promise<AutomationRun> {
      return read(await fetcher(`/api/automations/${encodeURIComponent(id)}/run`, { method: "POST" }), automationRunSchema);
    },
    async tasks(): Promise<TaskLink[]> {
      return (await read(await fetcher("/api/automations/tasks"), z.object({ links: z.array(taskLinkSchema) }))).links;
    },
    async status(): Promise<SchedulerStatus> {
      return read(await fetcher("/api/automations/status"), schedulerStatusSchema);
    },
  };
}

export type CalendarApi = ReturnType<typeof calendarApi>;

/** `2026-11-21T14:00:00-08:00`: a local time with its UTC offset, which the server reads as an aware datetime. */
export function isoWithOffset(date: Date): string {
  const pad = (n: number) => String(Math.abs(n)).padStart(2, "0");
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`;
}

/** Monday 00:00 of the week `date` is in (local time). */
export function weekStart(date: Date): Date {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  return start;
}

export function addDays(date: Date, days: number): Date {
  const moved = new Date(date);
  moved.setDate(moved.getDate() + days);
  return moved;
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** The week grid of the mockup: 8 AM to 10 PM at 36px an hour. */
export const GRID_FIRST_HOUR = 8;
export const GRID_LAST_HOUR = 22;
export const HOUR_PX = 36;

/** Where a span sits in its day column (px from the top, and height), clamped to the grid's hours. */
export function placement(start: Date, end: Date): { top: number; height: number } {
  const hours = (date: Date) => date.getHours() + date.getMinutes() / 60;
  const clamp = (h: number) => Math.min(GRID_LAST_HOUR, Math.max(GRID_FIRST_HOUR, h));
  const from = clamp(hours(start));
  const to = sameDay(start, end) ? clamp(hours(end)) : GRID_LAST_HOUR;
  return { top: (from - GRID_FIRST_HOUR) * HOUR_PX, height: Math.max(18, (to - from) * HOUR_PX) };
}

/** `Nov 16–22, 2026`, or across months `Nov 30 – Dec 6, 2026`. */
export function rangeLabel(start: Date): string {
  const end = addDays(start, 6);
  const month = (date: Date) => date.toLocaleDateString("en-US", { month: "short" });
  if (start.getMonth() === end.getMonth()) return `${month(start)} ${start.getDate()}–${end.getDate()}, ${end.getFullYear()}`;
  return `${month(start)} ${start.getDate()} – ${month(end)} ${end.getDate()}, ${end.getFullYear()}`;
}

/** `2:00–3:00`: hours without AM/PM, as the mockup's event cards show them. */
export function spanLabel(start: Date, end: Date): string {
  const short = (date: Date) => `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, "0")}`;
  return `${short(start)}–${short(end)}`;
}

/** What a trigger reads as in the detail panel. */
export function triggerLabel(automation: Pick<Automation, "trigger" | "offsetMinutes" | "rrule">): string {
  const offset = automation.offsetMinutes;
  const shift = offset === 0 ? "" : offset > 0 ? ` + ${offset} min` : ` − ${-offset} min`;
  if (automation.trigger === "event_start") return `At event start${shift}`;
  if (automation.trigger === "event_end") return `At event end${shift}`;
  return automation.rrule ? "On a schedule" : "Once";
}

/** How a task an automation started says where it came from: `Weekly notes · end of “Weekly notes”`. */
export function taskOrigin(link: TaskLink): string {
  if (!link.eventTitle) return link.title;
  const edge = link.trigger === "event_start" ? "start" : link.trigger === "event_end" ? "end" : undefined;
  return edge ? `${link.title} · ${edge} of \u201c${link.eventTitle}\u201d` : `${link.title} · \u201c${link.eventTitle}\u201d`;
}
