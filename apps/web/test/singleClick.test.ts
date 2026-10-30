import { describe, expect, it } from "vitest";
import type { Scheduler } from "../src/hoverPanel";
import { DOUBLE_CLICK_MS, SingleClick } from "../src/singleClick";

/** A clock the test turns by hand. */
function clock() {
  let now = 0;
  let next = 0;
  const timers = new Map<number, { at: number; callback: () => void }>();
  const scheduler: Scheduler<number> = {
    after: (callback, ms) => {
      timers.set(++next, { at: now + ms, callback });
      return next;
    },
    cancel: (handle) => void timers.delete(handle),
  };
  const advance = (ms: number) => {
    now += ms;
    for (const [handle, timer] of [...timers]) {
      if (timer.at <= now) {
        timers.delete(handle);
        timer.callback();
      }
    }
  };
  return { scheduler, advance };
}

describe("the orb's click", () => {
  it("acts only after the double-click interval has passed", () => {
    const { scheduler, advance } = clock();
    let acted = 0;
    new SingleClick(scheduler, () => acted++).click();
    advance(DOUBLE_CLICK_MS - 1);
    expect(acted).toBe(0);
    advance(1);
    expect(acted).toBe(1);
  });

  it("never acts when a double-click cancels it", () => {
    const { scheduler, advance } = clock();
    let acted = 0;
    const tap = new SingleClick(scheduler, () => acted++);
    tap.click();
    tap.click();
    tap.cancel();
    advance(DOUBLE_CLICK_MS * 4);
    expect(acted).toBe(0);
  });

  it("acts once for two slow taps inside one interval, and again for a later tap", () => {
    const { scheduler, advance } = clock();
    let acted = 0;
    const tap = new SingleClick(scheduler, () => acted++);
    tap.click();
    advance(DOUBLE_CLICK_MS - 1);
    tap.click();
    advance(DOUBLE_CLICK_MS);
    expect(acted).toBe(1);
    tap.click();
    advance(DOUBLE_CLICK_MS);
    expect(acted).toBe(2);
  });

  it("can be cancelled with nothing pending", () => {
    const { scheduler } = clock();
    expect(() => new SingleClick(scheduler, () => undefined).cancel()).not.toThrow();
  });
});
