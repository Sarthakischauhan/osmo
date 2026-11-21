import { describe, expect, it } from "vitest";
import { agentActivity } from "../src/compact";
import { liveThought, orbBubbleVisible } from "../src/orbActivity";

const quiet = agentActivity({}, [], undefined, 0);

describe("the thought bubble above the orb", () => {
  it("stays hidden while idle, listening or speaking, even if a thought was passed in", () => {
    for (const state of ["idle", "listening", "speaking"] as const) {
      expect(orbBubbleVisible(state, quiet, true)).toBe(false);
      expect(orbBubbleVisible(state, quiet, false)).toBe(false);
    }
  });

  it("appears while the agent is thinking or working, the same for every provider", () => {
    expect(orbBubbleVisible("thinking", quiet, true)).toBe(true);
    expect(orbBubbleVisible("working", quiet, true)).toBe(true);
  });

  it("does not appear for a thinking state that has no live thought and no run", () => {
    expect(orbBubbleVisible("thinking", quiet, false)).toBe(false);
  });

  it("keeps the picker clear when a run has no thinking tokens", () => {
    const running = { ...quiet, cards: [{ runId: "r", task: "Look up", status: "running" as const, detail: undefined, approval: undefined }], running: 1 };
    expect(orbBubbleVisible("working", running, false)).toBe(false);
    expect(orbBubbleVisible("idle", { ...quiet, elsewhere: 1 }, false)).toBe(false);
  });

  it("requires non-whitespace reasoning on the live turn", () => {
    const turn = { role: "assistant" as const, key: "a", text: "", thinking: true, activities: [], widgets: [], runs: [] };
    expect(liveThought([turn])).toBeUndefined();
    expect(liveThought([{ ...turn, reasoning: "  " }])).toBeUndefined();
    expect(liveThought([{ ...turn, reasoning: "Considering the options" }])?.reasoning).toBe("Considering the options");
  });
});
