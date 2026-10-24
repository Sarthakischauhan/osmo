import { describe, expect, it } from "vitest";
import { clientEventSchema, serverEventSchema } from "../src/protocol";

describe("wire protocol", () => {
  it("parses every client event", () => {
    for (const ev of [
      { type: "session.start", voice: "eve" },
      { type: "session.end" },
      { type: "interrupt" },
      { type: "widget.action", id: "w1", action: "select", value: "b" },
    ]) {
      expect(clientEventSchema.parse(ev)).toEqual(ev);
    }
  });

  it("rejects malformed events", () => {
    expect(clientEventSchema.safeParse({ type: "widget.action", id: "" }).success).toBe(false);
    expect(serverEventSchema.safeParse({ type: "state", state: "dancing" }).success).toBe(false);
  });
});
