import type { AgentRunStatus, ServerEvent } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { COMPLETED_LINGER_MS, type Runs, elapsed, fetchRuns, mergeListed, reduceRuns, visibleRuns } from "../src/lib/runs";

function play(events: ServerEvent[], start = 0): Runs {
  return events.reduce<Runs>((runs, event, i) => reduceRuns(runs, event, start + i * 1000), {});
}

const update = (status: AgentRunStatus, phase?: string): ServerEvent => ({
  type: "agent.update",
  runId: "r1",
  task: "draw a cat",
  status,
  phase,
});

describe("agent runs", () => {
  it("counts each tool the agent starts as a step and keeps the pending approval", () => {
    const runs = play([
      update("running", "Searching the web"),
      update("running", "Searching the web"),
      update("running", "Drawing a picture"),
      update("waiting_approval", "Drawing a picture"),
      { type: "approval.request", approvalId: "p1", runId: "r1", tool: "create_image", summary: "Drawing a picture", arguments: "{}" },
    ]);
    expect(runs.r1).toMatchObject({ steps: 3, status: "waiting_approval", approvalId: "p1", startedAt: 0 });
    const resolved = reduceRuns(runs, { type: "approval.resolved", approvalId: "p1", runId: "r1", approved: true }, 9000);
    expect(resolved.r1?.approvalId).toBeUndefined();
  });

  it("keeps a completed run in Running for two minutes", () => {
    const runs = play([update("running", "Thinking"), update("completed")]);
    expect(visibleRuns(runs, 1000 + COMPLETED_LINGER_MS - 1)).toHaveLength(1);
    expect(visibleRuns(runs, 1000 + COMPLETED_LINGER_MS)).toHaveLength(0);
  });

  it("keeps failed runs and drops cancelled ones", () => {
    expect(visibleRuns(play([update("failed")]), 10 ** 9)).toHaveLength(1);
    expect(visibleRuns(play([update("cancelled")]), 0)).toHaveLength(0);
  });

  it("formats elapsed time as mm:ss", () => {
    expect(elapsed(0, 724_000)).toBe("12:04");
  });
});

describe("the server's run list", () => {
  it("adds other threads' runs and keeps the step count the socket reported", async () => {
    const listed = await fetchRuns(120_000, async (url) => {
      expect(url).toBe("/api/runs?since=120");
      return Response.json({
        runs: [
          { runId: "r1", threadId: "t1", task: "draw a cat", status: "waiting_approval", createdAt: 10, updatedAt: 12 },
          { runId: "r2", threadId: "t2", task: "plan", status: "completed", result: "ok", createdAt: 20, updatedAt: 30 },
        ],
        approvals: [{ type: "approval.request", approvalId: "p1", runId: "r1", tool: "create_image", summary: "Drawing", arguments: "{}" }],
      });
    });
    const merged = mergeListed(play([update("running", "Drawing")]), listed);
    expect(merged.r1).toMatchObject({ threadId: "t1", steps: 1, status: "waiting_approval", approvalId: "p1" });
    expect(merged.r2).toMatchObject({ threadId: "t2", steps: 0, endedAt: 30_000, result: "ok" });
  });
});
