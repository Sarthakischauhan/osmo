import type { AgentRunStatus, ServerEvent } from "@osmo/osmo-composer";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApprovalPrompt } from "../src/components/ApprovalPrompt";
import { CompactAgentActivity } from "../src/CompactAgentActivity";
import { FloatingOrb } from "../src/FloatingOrb";
import { OrbPreview } from "../src/OrbPreview";
import { agentActivity } from "../src/compact";
import { emptyFeed, reduceFeed } from "../src/feed";
import { type Runs, reduceRuns } from "../src/lib/runs";

const update = (runId: string, status: AgentRunStatus, phase?: string, result?: string): ServerEvent => ({ type: "agent.update", runId, task: `task ${runId}`, status, phase, result });
const events: ServerEvent[] = [
  { type: "transcript.delta", role: "user", text: "Find trail shoes", itemId: "u1", final: true },
  { type: "transcript.delta", role: "assistant", text: "Looking at three shops now", itemId: "a1", final: true },
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

describe("the orb's preview box", () => {
  const html = renderToStaticMarkup(<OrbPreview turns={turns} runs={runs} activity={activity} onDecide={decide} />);

  it("shows the spoken lines and then the agent log", () => {
    for (const text of ["Find trail shoes", "Looking at three shops now", "task r1", "task r2", "task r3"]) expect(html).toContain(text);
    expect(html.indexOf("Find trail shoes")).toBeLessThan(html.indexOf("Looking at three shops now"));
    expect(html.indexOf("Looking at three shops now")).toBeLessThan(html.indexOf("task r1"));
  });

  it("renders the lines as the workspace does: the user's bubble and the assistant's avatar", () => {
    expect(html).toContain('class="turn user"');
    expect(html).toContain('class="turn assistant"');
    expect(html).toContain('class="avatar"');
  });

  it("keeps Approve / Deny, once, inside the waiting agent's entry", () => {
    const entry = html.split("<article").find((part) => part.includes("task r2"));
    expect(entry).toContain("Approve");
    expect(entry).toContain("Deny");
    expect(html.match(/Approve</g)).toHaveLength(1);
  });

  it("leaves tool logs, widgets and run cards to the workspace", () => {
    for (const text of ["Searching the web", "osmo-browser", "chat-subagent"]) expect(html).not.toContain(text);
  });

  it("is the agent log alone when nothing has been said, and the lines alone when no agent runs", () => {
    const noLines = renderToStaticMarkup(<OrbPreview turns={[]} runs={runs} activity={activity} onDecide={decide} />);
    expect(noLines).not.toContain("osmo-compact-transcript");
    expect(noLines).toContain("task r1");
    const none = agentActivity({}, turns, undefined, 0);
    const noAgents = renderToStaticMarkup(<OrbPreview turns={turns} runs={{}} activity={none} onDecide={decide} />);
    expect(noAgents).toContain("Find trail shoes");
    expect(noAgents).not.toContain("osmo-compact-agents");
  });
});

describe("the approval prompt's title", () => {
  const turn = turns.find((item) => item.role === "approval");
  const title = (step: number | undefined) => (turn?.role === "approval" ? renderToStaticMarkup(<ApprovalPrompt turn={turn} step={step} onDecide={() => undefined} />) : "");

  it("names the step when the run's steps are known", () => {
    expect(title(2)).toContain("Approve step 2?");
  });

  it("names the action when the steps are not known, instead of a step 0", () => {
    for (const step of [undefined, 0]) {
      expect(title(step)).toContain("Approve clicking buy?");
      expect(title(step)).not.toContain("step 0");
    }
  });
});

describe("the floating orb at rest", () => {
  it("is the orb alone: no header, Expand, End or log", () => {
    const html = renderToStaticMarkup(
      <FloatingOrb mode="idle" outputLevel={() => 0} runs={runs} turns={turns} activity={activity} theme="dark" onDecide={decide} onTalk={() => undefined} onExpand={() => undefined} placement={{ side: "below", orbLeft: 0, placed: 0 }} onBoxHeight={() => undefined} />,
    );
    expect(html).toContain("osmo-floating-orb");
    for (const text of ["Expand", "End", "<header", "osmo-compact-agents"]) expect(html).not.toContain(text);
  });
});

describe("where the floating orb puts its box", () => {
  const render = (placement: { side: "below" | "above"; orbLeft: number; placed: number }) =>
    renderToStaticMarkup(
      <FloatingOrb mode="idle" outputLevel={() => 0} runs={runs} turns={turns} activity={activity} theme="dark" onDecide={decide} onTalk={() => undefined} onExpand={() => undefined} placement={placement} onBoxHeight={() => undefined} />,
    );

  it("tells the layout which side the box is on and where the orb sits in the window", () => {
    const above = render({ side: "above", orbLeft: 124, placed: 200 });
    expect(above).toContain('data-side="above"');
    expect(above).toContain("--orb-left:124px");
    expect(render({ side: "below", orbLeft: 0, placed: 0 })).toContain('data-side="below"');
  });
});
