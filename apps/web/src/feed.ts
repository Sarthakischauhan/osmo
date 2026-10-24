import type { ServerEvent } from "@osmo/osmo-composer";

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

export type Turn =
  | { role: "user"; key: string; text: string }
  | { role: "assistant"; key: string; text: string; activities: Activity[]; widgets: CanvasWidget[] };

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

export function reduceFeed(feed: Feed, event: ServerEvent): Feed {
  switch (event.type) {
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
      return {
        ...feed,
        turns: feed.turns.map((turn) =>
          turn.role === "assistant" && turn.widgets.some((w) => w.id === event.id)
            ? { ...turn, widgets: turn.widgets.map((w) => (w.id === event.id ? { ...w, props: { ...w.props, ...event.patch } } : w)) }
            : turn,
        ),
      };
    case "state":
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
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? { ...value } : {};
}
