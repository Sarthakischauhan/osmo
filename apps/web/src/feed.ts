import { BARGE_IN, type ServerEvent } from "@osmo/osmo-composer";
import { isActionableWidget } from "@osmo/osmo-widgets";

/** The canvas feed: what the user said, and each assistant turn's line, tool activity and widgets. */
export interface Activity {
  label: string;
  done: boolean;
}

export interface CanvasWidget {
  id: string;
  type: string;
  props: Record<string, unknown>;
}

/** A background agent's risky step waiting in the feed for Approve / Deny; `resolved` once answered. */
export interface ApprovalTurn {
  role: "approval";
  key: string;
  approvalId: string;
  runId: string;
  tool: string;
  summary: string;
  arguments: string;
  resolved?: { approved: boolean; at: number };
}

export type Turn =
  | { role: "user"; key: string; text: string }
  | { role: "assistant"; key: string; text: string; activities: Activity[]; widgets: CanvasWidget[] }
  | ApprovalTurn;

type AssistantTurn = Extract<Turn, { role: "assistant" }>;

export interface Feed {
  turns: Turn[];
  seq: number;
}

export const emptyFeed: Feed = { turns: [], seq: 0 };

function withAssistant(feed: Feed, change: (turn: AssistantTurn) => AssistantTurn): Feed {
  const last = feed.turns[feed.turns.length - 1];
  if (last?.role === "assistant") {
    return { ...feed, turns: [...feed.turns.slice(0, -1), change(last)] };
  }
  const fresh: AssistantTurn = { role: "assistant", key: `a${feed.seq}`, text: "", activities: [], widgets: [] };
  return { seq: feed.seq + 1, turns: [...feed.turns, change(fresh)] };
}

function finishActivities(turn: AssistantTurn): AssistantTurn {
  return turn.activities.some((a) => !a.done)
    ? { ...turn, activities: turn.activities.map((a) => ({ ...a, done: true })) }
    : turn;
}

/** A stored thread's conversation as feed turns (agent notices are the voice model's, not shown). */
export function feedFromMessages(messages: readonly { role: "user" | "assistant" | "agent"; text: string }[]): Feed {
  let feed = emptyFeed;
  for (const message of messages) {
    if (message.role === "user") {
      feed = { seq: feed.seq + 1, turns: [...feed.turns, { role: "user", key: `u${feed.seq}`, text: message.text }] };
    } else if (message.role === "assistant") {
      const fresh: AssistantTurn = { role: "assistant", key: `a${feed.seq}`, text: message.text, activities: [], widgets: [] };
      feed = { seq: feed.seq + 1, turns: [...feed.turns, fresh] };
    }
  }
  return feed;
}

export function reduceFeed(feed: Feed, event: ServerEvent, now: number = Date.now()): Feed {
  switch (event.type) {
    case "approval.request": {
      const turn: ApprovalTurn = {
        role: "approval",
        key: `p${feed.seq}`,
        approvalId: event.approvalId,
        runId: event.runId,
        tool: event.tool,
        summary: event.summary,
        arguments: event.arguments,
      };
      return { seq: feed.seq + 1, turns: [...feed.turns, turn] };
    }
    case "approval.resolved":
      return {
        ...feed,
        turns: feed.turns.map((turn) =>
          turn.role === "approval" && turn.approvalId === event.approvalId && turn.resolved === undefined
            ? { ...turn, resolved: { approved: event.approved, at: now } }
            : turn,
        ),
      };
    case "thread":
    case "agent.update":
      // The sidebar and the Running list show these.
      return feed;
    case "transcript.delta":
      if (event.role === "user") {
        return { seq: feed.seq + 1, turns: [...feed.turns, { role: "user", key: `u${feed.seq}`, text: event.text }] };
      }
      return withAssistant(feed, (turn) => ({ ...turn, text: turn.text + event.text }));
    case "widget.render":
      return withAssistant(feed, (turn) => ({
        ...finishActivities(turn),
        widgets: [...turn.widgets, { id: event.widget.id, type: event.widget.type, props: asRecord(event.widget.props) }],
      }));
    case "widget.update":
      return updateWidget(feed, event.id, event.patch);
    case "state":
      // BARGE_IN only tells the client to flush queued audio; it is neither a step nor the end of one.
      if (event.detail === BARGE_IN) return feed;
      if (event.state === "working" && event.detail) {
        const label = event.detail;
        return withAssistant(feed, (turn) => ({ ...turn, activities: [...turn.activities, { label, done: false }] }));
      }
      {
        const last = feed.turns[feed.turns.length - 1];
        return last?.role === "assistant" && last.activities.some((a) => !a.done)
          ? withAssistant(feed, finishActivities)
          : feed;
      }
    case "desktop.request":
    case "models":
    case "voice.configured":
    case "voice.preview":
      // Voice picker events; the feed has nothing to show for them.
      return feed;
  }
}

/**
 * Patch exactly one widget: the newest one carrying this id. Older widgets stay frozen even if an
 * id were ever reused (for example by a server session started after End), so an update can never
 * reach back and rewrite something already above the current message.
 */
function updateWidget(feed: Feed, id: string, patch: Record<string, unknown>): Feed {
  for (let t = feed.turns.length - 1; t >= 0; t--) {
    const turn = feed.turns[t];
    if (turn?.role !== "assistant") continue;
    let w = -1;
    turn.widgets.forEach((widget, i) => {
      if (widget.id === id) w = i;
    });
    if (w === -1) continue;
    const widgets = turn.widgets.map((widget, i) => (i === w ? { ...widget, props: { ...widget.props, ...patch } } : widget));
    const turns = feed.turns.map((existing, i) => (i === t ? { ...turn, widgets } : existing));
    return { ...feed, turns };
  }
  return feed;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? { ...value } : {};
}

/**
 * The id of the widget the user still owes an answer to, if any: the newest actionable widget
 * (options, or a card with buttons) that was not tapped and has no spoken user turn after it.
 * Answering by voice counts, because the next user turn is that answer.
 */
export function pendingChoice(feed: Feed, answered: ReadonlySet<string>): string | undefined {
  for (let t = feed.turns.length - 1; t >= 0; t--) {
    const turn = feed.turns[t];
    if (!turn) continue;
    if (turn.role === "user") return undefined;
    if (turn.role === "approval") continue;
    for (let w = turn.widgets.length - 1; w >= 0; w--) {
      const widget = turn.widgets[w];
      if (widget && isActionableWidget(widget.type, widget.props)) {
        return answered.has(widget.id) ? undefined : widget.id;
      }
    }
  }
  return undefined;
}
