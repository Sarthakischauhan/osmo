import { describe, expect, it } from "vitest";
import { PRESETS, initialFrame, orbMode, step } from "../src/orbVisual";

describe("orbMode", () => {
  it("is amber only while connected, not busy, and a choice is pending", () => {
    expect(orbMode("connected", "listening", true)).toBe("awaiting");
    expect(orbMode("connected", "idle", true)).toBe("awaiting");
    expect(orbMode("connected", "listening", false)).toBe("listening");
    expect(orbMode("disconnected", "idle", true)).toBe("idle");
    expect(orbMode("connected", "speaking", true)).toBe("speaking");
    expect(orbMode("connected", "thinking", true)).toBe("thinking");
    expect(orbMode("connected", "working", false)).toBe("thinking");
    expect(orbMode("connecting", "idle", false)).toBe("connecting");
  });

  it("keeps idle and listening off amber, and thinking distinct from speaking", () => {
    const amber = PRESETS.awaiting.colors[1];
    expect(PRESETS.idle.colors[1]).not.toEqual(amber);
    expect(PRESETS.listening.colors[1]).not.toEqual(amber);
    expect(PRESETS.thinking.colors).not.toEqual(PRESETS.speaking.colors);
  });
});

describe("step", () => {
  it("follows the output level only while speaking, with fast attack and slower release", () => {
    const frame = initialFrame("speaking");
    step(frame, "speaking", 1, 0.05, false);
    const attacked = frame.level;
    expect(attacked).toBeGreaterThan(0.5);
    step(frame, "speaking", 0, 0.05, false);
    expect(frame.level).toBeGreaterThan(attacked * 0.6);
    const listening = initialFrame("listening");
    step(listening, "listening", 1, 0.05, false);
    expect(listening.level).toBe(0);
  });

  it("freezes motion when reduced motion is requested", () => {
    const frame = initialFrame("speaking");
    step(frame, "speaking", 0, 1, true);
    expect(frame.time).toBe(0);
    expect(frame.preset.colors).toEqual(PRESETS.speaking.colors);
  });
});
