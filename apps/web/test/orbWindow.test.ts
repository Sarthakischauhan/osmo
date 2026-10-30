import type { ServerEvent } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { NO_SESSION, parseServerEvent } from "../src/desktopSession";
import { emptyFeed } from "../src/feed";
import { reduceOrbFeed, savedStart } from "../src/orbWindow";

const approval = (approvalId: string, runId: string): Extract<ServerEvent, { type: "approval.request" }> => ({ type: "approval.request", approvalId, runId, tool: "browser_click", summary: "Click Buy", arguments: "{}" });
const waiting = (feed: ReturnType<typeof reduceOrbFeed>) => feed.turns.flatMap((turn) => (turn.role === "approval" && turn.resolved === undefined ? [turn.approvalId] : []));

describe("the orb window's approvals", () => {
  it("shows the approvals the server lists, and only those", () => {
    const first = reduceOrbFeed(emptyFeed, { kind: "listed", approvals: [approval("a1", "r1"), approval("a2", "r2")] });
    expect(waiting(first)).toEqual(["a1", "a2"]);
    expect(waiting(reduceOrbFeed(first, { kind: "listed", approvals: [approval("a2", "r2")] }))).toEqual(["a2"]);
  });

  it("takes a live request and a decision from the session's events", () => {
    const asked = reduceOrbFeed(emptyFeed, { kind: "event", event: approval("a3", "r3") });
    expect(waiting(asked)).toEqual(["a3"]);
    const decided = reduceOrbFeed(asked, { kind: "event", event: { type: "approval.resolved", approvalId: "a3", runId: "r3", approved: true } });
    expect(waiting(decided)).toEqual([]);
  });
});

describe("the session the orb starts", () => {
  it("uses the workspace picker's saved pick", () => {
    expect(savedStart(JSON.stringify({ model: "grok-voice", voice: "eve", effort: "low" }))).toEqual({ model: "grok-voice", voice: "eve", effort: "low" });
    expect(savedStart(JSON.stringify({ model: "m", voice: "v" }))).toEqual({ model: "m", voice: "v" });
  });

  it("starts with the server's defaults when nothing usable is saved", () => {
    for (const saved of [null, "", "{", "null", "[]", JSON.stringify({ model: "m" }), JSON.stringify({ model: "m", voice: "v", effort: "max" })]) expect(savedStart(saved)).toEqual({});
  });
});

describe("server events from the main process", () => {
  it("are accepted when they match the protocol", () => {
    expect(parseServerEvent({ type: "state", state: "listening" })).toEqual({ type: "state", state: "listening" });
  });

  it("are dropped when they do not", () => {
    for (const raw of [null, "x", {}, { type: "state", state: "dancing" }, { type: "unknown" }]) expect(parseServerEvent(raw)).toBeUndefined();
  });

  it("start from a window with no session", () => {
    expect(NO_SESSION).toMatchObject({ connection: "disconnected", state: "idle", models: [] });
  });
});
