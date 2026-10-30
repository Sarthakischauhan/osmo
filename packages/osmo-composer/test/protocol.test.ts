import { describe, expect, it } from "vitest";
import { clientEventSchema, serverEventSchema } from "../src/protocol";

describe("wire protocol", () => {
  it("parses every client event", () => {
    for (const ev of [
      { type: "session.start", voice: "eve" },
      { type: "session.end" },
      { type: "interrupt" },
      { type: "widget.action", id: "w1", action: "select", value: "b" },
      { type: "browser.click", id: "w1", x: 0.5, y: 0.25 },
      { type: "browser.scroll", id: "w1", x: 0, y: 1, deltaX: 0, deltaY: -120 },
      { type: "browser.key", id: "w1", key: "Enter", code: "Enter", keyCode: 13 },
      { type: "browser.close", id: "w1" },
    ]) {
      expect(clientEventSchema.parse(ev)).toEqual(ev);
    }
  });

  it("rejects malformed events", () => {
    expect(clientEventSchema.safeParse({ type: "widget.action", id: "" }).success).toBe(false);
    expect(serverEventSchema.safeParse({ type: "state", state: "dancing" }).success).toBe(false);
    // The page is addressed in fractions of what is displayed; a key is something.
    expect(clientEventSchema.safeParse({ type: "browser.click", id: "w1", x: 1.5, y: 0.5 }).success).toBe(false);
    expect(clientEventSchema.safeParse({ type: "browser.click", id: "", x: 0.5, y: 0.5 }).success).toBe(false);
    expect(clientEventSchema.safeParse({ type: "browser.key", id: "w1", key: "", code: "", keyCode: 0 }).success).toBe(false);
    expect(clientEventSchema.safeParse({ type: "browser.key", id: "w1", key: "a", code: "KeyA", keyCode: -1 }).success).toBe(false);
  });
});
