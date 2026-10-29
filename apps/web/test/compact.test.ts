import type { AgentRunStatus, ServerEvent } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { agentActivity } from "../src/compact";
import { type Feed, emptyFeed, reduceFeed } from "../src/feed";
import { COMPLETED_LINGER_MS, type Runs, reduceRuns } from "../src/lib/runs";

const feedOf = (events: ServerEvent[]): Feed => events.reduce<Feed>((feed, event) => reduceFeed(feed, event, 0), emptyFeed);
const runsOf = (events: ServerEvent[], ids: Record<string, string> = {}): Runs => {
  const runs = events.reduce<Runs>((all, event, i) => reduceRuns(all, event, i * 1000), {});
  return Object.fromEntries(Object.entries(runs).map(([id, run]) => [id, { ...run, threadId: ids[id] }]));
};
const update = (runId: string, status: AgentRunStatus, phase?: string, result?: string): ServerEvent => ({ type: "agent.update", runId, task: `task ${runId}`, status, phase, result });
const approval = (runId: string): ServerEvent => ({ type: "approval.request", approvalId: `p-${runId}`, runId, tool: "browser_click", summary: "Clicking Buy", arguments: "{}" });

describe("compact agent activity", () => {
  it("puts a run waiting for approval first, then working runs, with the approval to answer", () => {
    const events = [update("r1", "running", "Reading"), update("r2", "running", "Clicking"), update("r2", "waiting_approval", "Clicking"), approval("r2")];
    const activity = agentActivity(runsOf(events), feedOf(events).turns, undefined, 5000);
    expect(activity.cards.map((card) => [card.runId, card.status])).toEqual([["r2", "waiting_approval"], ["r1", "running"]]);
    expect(activity.cards[0]?.approval?.approvalId).toBe("p-r2");
    expect(activity.cards[1]?.detail).toBe("Reading");
    expect(activity).toMatchObject({ running: 2, needingApproval: 1, elsewhere: 0 });
  });

  it("shows results, errors and cancellations, and drops finished runs after the usual two minutes", () => {
    const events = [update("r1", "completed", undefined, "Found 3 shoes"), update("r2", "failed", undefined, "Site unreachable"), update("r3", "cancelled")];
    const runs = runsOf(events);
    const soon = agentActivity(runs, [], undefined, 2500);
    expect(soon.cards.map((card) => [card.runId, card.detail])).toEqual([["r2", "Site unreachable"], ["r1", "Found 3 shoes"], ["r3", "Cancelled"]]);
    const later = agentActivity(runs, [], undefined, 3000 + COMPLETED_LINGER_MS);
    expect(later.cards.map((card) => card.runId)).toEqual(["r2"]);
  });

  it("keeps this thread's runs as cards and counts the other threads' working runs", () => {
    const events = [update("r1", "running", "Reading"), update("r2", "running", "Reading"), update("r3", "completed", undefined, "done")];
    const runs = runsOf(events, { r1: "t1", r2: "t2", r3: "t2" });
    expect(agentActivity(runs, [], "t1", 5000)).toMatchObject({ cards: [{ runId: "r1" }], running: 1, elsewhere: 1 });
  });

  it("offers Approve / Deny only for an approval not yet answered", () => {
    const events: ServerEvent[] = [update("r1", "waiting_approval", "Clicking"), approval("r1"), { type: "approval.resolved", approvalId: "p-r1", runId: "r1", approved: true }];
    const activity = agentActivity(runsOf(events), feedOf(events).turns, undefined, 5000);
    expect(activity.cards[0]?.approval).toBeUndefined();
  });
});
