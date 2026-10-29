import type { ServerEvent } from "@osmo/osmo-composer";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompactPanel } from "../src/CompactPanel";
import { agentActivity } from "../src/compact";
import { emptyFeed, reduceFeed } from "../src/feed";
import { reduceRuns, type Runs } from "../src/lib/runs";

const events: ServerEvent[] = [
  { type: "transcript.delta", role: "user", text: "Find trail shoes", itemId: "u1", final: true },
  { type: "transcript.delta", role: "assistant", text: "Looking now", itemId: "a1", final: false },
  { type: "state", state: "working", detail: "Searching the web" },
  { type: "widget.render", widget: { id: "w1", type: "browser", props: { url: "http://shop" } } },
  { type: "agent.update", runId: "r1", task: "Compare shoes", status: "waiting_approval", phase: "Clicking Buy" },
  { type: "approval.request", approvalId: "p1", runId: "r1", tool: "browser_click", summary: "Clicking Buy", arguments: "{}" },
];

function render(connected: boolean): string {
  const turns = events.reduce(reduceFeed, emptyFeed).turns;
  const runs = events.reduce<Runs>((all, event, i) => reduceRuns(all, event, i), {});
  const activity = agentActivity(runs, turns, undefined, 10);
  const none = async () => undefined;
  return renderToStaticMarkup(<CompactPanel turns={turns} runs={runs} activity={activity} status="Thinking" theme="dark" connected={connected} onDecide={none} onExpand={() => undefined} onEnd={() => undefined} />);
}

describe("the compact panel", () => {
  const html = render(true);

  it("has Osmo, the session status and Expand in its header", () => {
    expect(html).toMatch(/<strong>Osmo<\/strong>/);
    expect(html).toContain("Thinking");
    expect(html).toContain("Expand");
  });

  it("shows the spoken lines and the agent's summary with Approve / Deny", () => {
    expect(html).toContain("Find trail shoes");
    expect(html).toContain("Looking now");
    expect(html).toContain("Compare shoes");
    expect(html).toContain("needs approval");
    expect(html).toContain("Approve");
    expect(html).toContain("Deny");
  });

  it("leaves tool logs, the browser widget and run cards to the workspace", () => {
    expect(html).not.toContain("Searching the web");
    expect(html).not.toContain("osmo-browser");
    expect(html).not.toContain("chat-subagent");
  });

  it("offers End only while a session is live", () => {
    expect(html).toContain("End");
    expect(render(false)).not.toContain("End</button>");
  });
});
