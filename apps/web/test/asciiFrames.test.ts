import { describe, expect, it } from "vitest";
import { FRAMES, getBand, type Band } from "../src/lib/asciiFrames";
import { twinkle } from "../src/components/home/AsciiHome";

const at = (h: number, m = 0) => new Date(2026, 9, 26, h, m);

describe("getBand", () => {
  const cases: Array<[number, number, Band]> = [
    [4, 59, "night"], [5, 0, "dawn"], [6, 59, "dawn"], [7, 0, "morning"], [10, 59, "morning"],
    [11, 0, "midday"], [15, 59, "midday"], [16, 0, "dusk"], [18, 59, "dusk"], [19, 0, "evening"],
    [20, 59, "evening"], [21, 0, "night"], [23, 59, "night"], [0, 0, "night"],
  ];
  it.each(cases)("%i:%i is %s", (h, m, band) => expect(getBand(at(h, m))).toBe(band));
});

describe("twinkle", () => {
  it("changes 1-2 star characters and nothing else", () => {
    let i = 0;
    const seq = [0.9, 0.1, 0.6];
    const out = twinkle(FRAMES.night, () => seq[i++ % seq.length] ?? 0);
    expect(out.length).toBe(FRAMES.night.length);
    const diffs = [...out].filter((c, k) => c !== FRAMES.night[k]);
    expect(diffs.length).toBeGreaterThanOrEqual(1);
    expect(diffs.length).toBeLessThanOrEqual(2);
    expect(out.replace(/[.*+]/g, "")).toBe(FRAMES.night.replace(/[.*+]/g, ""));
  });
});
