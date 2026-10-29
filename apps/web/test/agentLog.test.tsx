import type { AgentRunStatus, ServerEvent } from "@osmo/osmo-composer";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompactAgentActivity } from "../src/CompactAgentActivity";
import { FloatingOrb } from "../src/FloatingOrb";
import { agentActivity } from "../src/compact";
import { emptyFeed, reduceFeed } from "../src/feed";
import { type Runs, reduceRuns } from "../src/lib/runs";

const update = (runId: string, status: AgentRunStatus, phase?: string, result?: string): ServerEvent => ({ type: "agent.update", runId, task: `task ${runId}`, status, phase, result });
const events: ServerEvent[] = [
  { type: "transcript.delta", role: "user", text: "Find trail shoes", itemId: "u1", final: true },
  { type: "state", state: "working", detail: "Searching the web" },
  { type: "widget.render", widget: { id: "w1", type: "browser", props: { url: "http://shop" } } },
  update("r1", "running", "Reading the shop"),
  update("r2", "waiting_approval", "Clicking Buy"),
  { type: "approval.request", approvalId: "p2", runId: "r2", tool: "browser_click", summary: "Clicking Buy", arguments: "{}" },
  update("r3", "failed", undefined, "The shop did not load"),
];
const turns = events.reduce(reduceFeed, emptyFeed).turns;
const runs = events.reduce<Runs>((all, event, i) => reduceRuns(all, event, i), {});
const activity = agentActivity(runs, turns, undefined, 10);
const decide = async () => undefined;

describe("the agent log", () => {
  const html = renderToStaticMarkup(<CompactAgentActivity activity={activity} runs={runs} onDecide={decide} />);

  it("has one entry per run with its status and detail", () => {
    for (const text of ["task r1", "Reading the shop", "task r2", "task r3", "The shop did not load", "working", "needs approval", "failed"]) expect(html).toContain(text);
    expect(html).toContain("2 active (1 waiting for approval)");
  });

  it("keeps Approve / Deny inside the entry that waits", () => {
    const entry = html.split("<article").find((part) => part.includes("task r2"));
    expect(entry).toContain("Approve");
    expect(entry).toContain("Deny");
    expect(html.match(/Approve</g)).toHaveLength(1);
  });

  it("leaves the transcript, tool logs and widgets out", () => {
    for (const text of ["Find trail shoes", "Searching the web", "osmo-browser"]) expect(html).not.toContain(text);
  });

  it("renders nothing when there are no runs", () => {
    expect(renderToStaticMarkup(<CompactAgentActivity activity={agentActivity({}, [], undefined, 0)} runs={{}} onDecide={decide} />)).toBe("");
  });
});

describe("the floating orb at rest", () => {
  it("is the orb alone: no header, Expand, End or log", () => {
    const html = renderToStaticMarkup(<FloatingOrb mode="idle" outputLevel={() => 0} runs={runs} activity={activity} theme="dark" onDecide={decide} onTalk={() => undefined} />);
    expect(html).toContain("osmo-floating-orb");
    for (const text of ["Expand", "End", "<header", "osmo-compact-agents"]) expect(html).not.toContain(text);
  });
});
