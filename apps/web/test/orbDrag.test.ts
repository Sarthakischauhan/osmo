import { describe, expect, it } from "vitest";
import { DRAG_THRESHOLD, OrbDrag } from "../src/orbDrag";

describe("OrbDrag", () => {
  it("a press that barely moves is a tap and moves nothing", () => {
    const moves: [number, number][] = [];
    const drag = new OrbDrag((dx, dy) => moves.push([dx, dy]));
    drag.down(100, 100);
    expect(drag.moveTo(102, 101)).toBe(false);
    drag.up();
    expect(moves).toEqual([]);
    expect(drag.takeClick()).toBe(true);
  });

  it("past the threshold it moves the window by each step, and the click that ends it is not a tap", () => {
    const moves: [number, number][] = [];
    const drag = new OrbDrag((dx, dy) => moves.push([dx, dy]));
    drag.down(100, 100);
    expect(drag.moveTo(100 + DRAG_THRESHOLD, 100)).toBe(true);
    expect(drag.active).toBe(true);
    drag.moveTo(110, 90);
    drag.moveTo(110, 90);
    drag.up();
    expect(moves).toEqual([[DRAG_THRESHOLD, 0], [10 - DRAG_THRESHOLD, -10]]);
    expect(drag.takeClick()).toBe(false);
    // Only that click: the next press is a tap again.
    drag.down(0, 0);
    drag.up();
    expect(drag.takeClick()).toBe(true);
  });

  it("ignores pointer moves with no press", () => {
    const moves: unknown[] = [];
    const drag = new OrbDrag(() => moves.push(1));
    expect(drag.moveTo(50, 50)).toBe(false);
    expect(moves).toEqual([]);
  });
});
