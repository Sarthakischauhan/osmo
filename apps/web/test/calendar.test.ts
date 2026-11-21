import { describe, expect, it } from "vitest";
import { addDays, calendarApi, isoWithOffset, placement, rangeLabel, spanLabel, triggerLabel, weekStart } from "../src/lib/calendar";

describe("the week grid", () => {
  it("starts weeks on Monday and labels the range like the mockup", () => {
    const start = weekStart(new Date(2026, 10, 21, 14, 30));
    expect([start.getDate(), start.getDay(), start.getHours()]).toEqual([16, 1, 0]);
    expect(rangeLabel(start)).toBe("Nov 16–22, 2026");
    expect(rangeLabel(weekStart(new Date(2026, 11, 2)))).toBe("Nov 30 – Dec 6, 2026");
    expect(addDays(start, 7).getDate()).toBe(23);
  });

  it("places spans at 36px an hour from 8 AM and clamps them to 8 AM–10 PM", () => {
    expect(placement(new Date(2026, 10, 21, 14), new Date(2026, 10, 21, 15))).toEqual({ top: 216, height: 36 });
    expect(placement(new Date(2026, 10, 21, 9, 30), new Date(2026, 10, 21, 10, 30))).toEqual({ top: 54, height: 36 });
    expect(placement(new Date(2026, 10, 21, 6), new Date(2026, 10, 21, 9))).toEqual({ top: 0, height: 36 });
    expect(placement(new Date(2026, 10, 21, 23), new Date(2026, 10, 21, 23, 30)).top).toBe(504);
    expect(spanLabel(new Date(2026, 10, 21, 14), new Date(2026, 10, 21, 15))).toBe("2:00–3:00");
  });

  it("labels triggers and writes local times with their offset", () => {
    expect(triggerLabel({ trigger: "event_end", offsetMinutes: 0, rrule: null })).toBe("At event end");
    expect(triggerLabel({ trigger: "event_start", offsetMinutes: -10, rrule: null })).toBe("At event start − 10 min");
    expect(triggerLabel({ trigger: "time", offsetMinutes: 0, rrule: "FREQ=DAILY" })).toBe("On a schedule");
    expect(isoWithOffset(new Date(2026, 10, 21, 14))).toMatch(/^2026-11-21T14:00:00[+-]\d\d:\d\d$/);
  });
});

describe("the calendar API", () => {
  it("asks for a week with offsets and reads occurrences and ticks", async () => {
    const asked: string[] = [];
    const api = calendarApi(async (input) => {
      asked.push(input);
      return new Response(JSON.stringify({ occurrences: [{ eventId: "e", title: "Run", start: "2026-11-22T11:00:00-08:00", end: "2026-11-22T12:00:00-08:00" }], events: [], ticks: [{ automationId: "a", title: "Brief", at: "2026-11-16T08:00:00-08:00" }] }));
    });
    const week = await api.week(new Date(2026, 10, 16), new Date(2026, 10, 23));
    expect(week.occurrences[0]?.title).toBe("Run");
    expect(week.ticks[0]?.title).toBe("Brief");
    expect(asked[0]).toContain("/api/calendar/events?start=2026-11-16T00%3A00%3A00");
  });
});
