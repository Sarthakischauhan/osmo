import { describe, expect, it } from "vitest";
import { greeting, openingCue } from "../src/components/home/AsciiHome";
import { getBand, type Band } from "../src/lib/asciiFrames";

const at = (h: number, m = 0) => new Date(2026, 9, 26, h, m);

describe("getBand", () => {
  const cases: Array<[number, number, Band]> = [
    [4, 59, "night"], [5, 0, "dawn"], [6, 59, "dawn"], [7, 0, "morning"], [10, 59, "morning"],
    [11, 0, "midday"], [15, 59, "midday"], [16, 0, "dusk"], [18, 59, "dusk"], [19, 0, "evening"],
    [20, 59, "evening"], [21, 0, "night"], [23, 59, "night"], [0, 0, "night"],
  ];
  it.each(cases)("%i:%i is %s", (h, m, band) => expect(getBand(at(h, m))).toBe(band));
});

describe("greeting", () => {
  it("names the part of day and optional user", () => {
    expect(greeting(at(9).getTime(), undefined)).toBe("Good morning");
    expect(greeting(at(13).getTime(), "Sarthak")).toBe("Good afternoon, Sarthak");
    expect(greeting(at(20).getTime(), "Sarthak")).toBe("Good evening, Sarthak");
  });
});

describe("openingCue", () => {
  it("tracks the session connection before voice starts", () => {
    expect(openingCue("disconnected")).toBe("Tap the orb or press Space to talk");
    expect(openingCue("connecting")).toBe("Opening your session…");
  });

  it("mirrors live voice state once connected", () => {
    expect(openingCue("connected", "idle")).toBe("Ready · tap the orb or press Space");
    expect(openingCue("connected", "listening")).toBe("Listening · just talk");
    expect(openingCue("connected", "thinking")).toBe("Thinking…");
    expect(openingCue("connected", "working")).toBe("Working…");
    expect(openingCue("connected", "speaking")).toBe("Speaking · tap the orb to interrupt");
  });
});
