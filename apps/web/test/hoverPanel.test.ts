import { describe, expect, it } from "vitest";
import { CLOSE_DELAY_MS, HoverPanel, OPEN_DELAY_MS, type Scheduler } from "../src/hoverPanel";

/** Timers on a clock the test moves; nothing waits. */
class Clock implements Scheduler<number> {
  now = 0;
  private next = 0;
  private readonly timers = new Map<number, { at: number; run: () => void }>();
  after(run: () => void, ms: number): number {
    this.timers.set(++this.next, { at: this.now + ms, run });
    return this.next;
  }
  cancel(handle: number): void {
    this.timers.delete(handle);
  }
  advance(ms: number): void {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.now = due[1].at;
      due[1].run();
    }
    this.now = end;
  }
}

function panel() {
  const clock = new Clock();
  const changes: boolean[] = [];
  const hover = new HoverPanel(clock, (open) => changes.push(open));
  return { clock, hover, changes };
}

describe("the hover panel's timing", () => {
  it("opens 200 ms after the pointer reaches the orb, not before", () => {
    const { clock, hover, changes } = panel();
    hover.enter("orb");
    clock.advance(OPEN_DELAY_MS - 1);
    expect(hover.open).toBe(false);
    clock.advance(1);
    expect(hover.open).toBe(true);
    expect(changes).toEqual([true]);
  });

  it("does not open for a pointer that only passes over the orb", () => {
    const { clock, hover } = panel();
    hover.enter("orb");
    clock.advance(OPEN_DELAY_MS - 50);
    hover.leave("orb");
    clock.advance(1000);
    expect(hover.open).toBe(false);
  });

  it("closes 500 ms after the pointer left the orb and the panel", () => {
    const { clock, hover } = panel();
    hover.enter("orb");
    clock.advance(OPEN_DELAY_MS);
    hover.leave("orb");
    clock.advance(CLOSE_DELAY_MS - 1);
    expect(hover.open).toBe(true);
    clock.advance(1);
    expect(hover.open).toBe(false);
  });

  it("stays open while the pointer crosses from the orb to the panel, and counts the close delay from the last exit", () => {
    const { clock, hover, changes } = panel();
    hover.enter("orb");
    clock.advance(OPEN_DELAY_MS);
    hover.leave("orb");
    clock.advance(300);
    hover.enter("panel");
    clock.advance(5000);
    expect(hover.open).toBe(true);
    hover.leave("panel");
    clock.advance(CLOSE_DELAY_MS);
    expect(hover.open).toBe(false);
    expect(changes).toEqual([true, false]);
  });

  it("is pinned open by interaction", () => {
    const { clock, hover } = panel();
    hover.enter("orb");
    clock.advance(OPEN_DELAY_MS);
    hover.pin();
    hover.leave("orb");
    clock.advance(60_000);
    expect(hover.open).toBe(true);
    hover.release();
    clock.advance(CLOSE_DELAY_MS);
    expect(hover.open).toBe(false);
  });

  it("shows itself for an approval and stays until it is answered", () => {
    const { clock, hover } = panel();
    hover.approvalWaiting(true);
    expect(hover.open).toBe(true);
    clock.advance(60_000);
    expect(hover.open).toBe(true);
    hover.approvalWaiting(false);
    clock.advance(CLOSE_DELAY_MS);
    expect(hover.open).toBe(false);
  });

  it("does nothing once disposed", () => {
    const { clock, hover, changes } = panel();
    hover.enter("orb");
    hover.dispose();
    clock.advance(1000);
    expect(changes).toEqual([]);
  });
});
