import { type AgentRunStatus, BARGE_IN, type ServerEvent } from "@osmo/osmo-composer";
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

/** A background agent run (`run_agent`), shown as a subagent card under the answer that started it. */
export interface RunCard {
  runId: string;
  task: string;
  status: AgentRunStatus;
  /** Each step it started, in order. */
  phases: string[];
  result?: string;
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
  | UserTurn
  | { role: "assistant"; key: string; text: string; itemId?: string; activities: Activity[]; widgets: CanvasWidget[]; runs: RunCard[] }
  | ApprovalTurn;

/** `pending` while the utterance's transcript is still on its way. */
export interface UserTurn {
  role: "user";
  key: string;
  text: string;
  itemId?: string;
  pending?: boolean;
}

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
  const fresh: AssistantTurn = { role: "assistant", key: `a${feed.seq}`, text: "", activities: [], widgets: [], runs: [] };
  return { seq: feed.seq + 1, turns: [...feed.turns, change(fresh)] };
}

function lastAssistantIndex(turns: readonly Turn[]): number {
  for (let i = turns.length - 1; i >= 0; i--) if (turns[i]?.role === "assistant") return i;
  return -1;
}

function withRun(feed: Feed, at: number, run: RunCard): Feed {
  const turn = feed.turns[at];
  if (turn?.role !== "assistant") return feed;
  const runs = turn.runs.some((r) => r.runId === run.runId) ? turn.runs.map((r) => (r.runId === run.runId ? run : r)) : [...turn.runs, run];
  return { ...feed, turns: feed.turns.map((existing, i) => (i === at ? { ...turn, runs } : existing)) };
}

/**
 * A run's card stays under the answer that started it: the server reports a run as soon as
 * run_agent starts it, while that answer is still the latest; later updates find the card.
 */
function trackRun(feed: Feed, event: Extract<ServerEvent, { type: "agent.update" }>): Feed {
  const at = feed.turns.findIndex((turn) => turn.role === "assistant" && turn.runs.some((run) => run.runId === event.runId));
  const turn = feed.turns[at];
  const previous = turn?.role === "assistant" ? turn.runs.find((run) => run.runId === event.runId) : undefined;
  const phases = previous?.phases ?? [];
  const run: RunCard = {
    runId: event.runId,
    task: event.task,
    status: event.status,
    phases: event.status === "running" && event.phase ? [...phases, event.phase] : phases,
    result: event.result ?? previous?.result,
  };
  if (at !== -1) return withRun(feed, at, run);
  const last = lastAssistantIndex(feed.turns);
  return last === -1 ? withAssistant(feed, (open) => ({ ...open, runs: [run] })) : withRun(feed, last, run);
}

function finishActivities(turn: AssistantTurn): AssistantTurn {
  return turn.activities.some((a) => !a.done)
    ? { ...turn, activities: turn.activities.map((a) => ({ ...a, done: true })) }
    : turn;
}

interface StoredRun {
  runId: string;
  task: string;
  status: AgentRunStatus;
  result?: string | null;
  createdAt: number;
}

/**
 * A stored thread's conversation as feed turns (agent notices are the voice model's, not shown),
 * each run's card under the first answer to what the user said before it started.
 */
export function feedFromMessages(
  messages: readonly { role: "user" | "assistant" | "agent"; text: string; createdAt?: number }[],
  runs: readonly StoredRun[] = [],
): Feed {
  let feed = emptyFeed;
  const said: number[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      feed = { seq: feed.seq + 1, turns: [...feed.turns, { role: "user", key: `u${feed.seq}`, text: message.text }] };
    } else if (message.role === "assistant") {
      const fresh: AssistantTurn = { role: "assistant", key: `a${feed.seq}`, text: message.text, activities: [], widgets: [], runs: [] };
      feed = { seq: feed.seq + 1, turns: [...feed.turns, fresh] };
    } else {
      continue;
    }
    said.push(message.createdAt ?? 0);
  }
  for (const run of runs) {
    let asked = -1;
    feed.turns.forEach((turn, i) => {
      if (turn.role === "user" && (said[i] ?? 0) <= run.createdAt) asked = i;
    });
    let at = feed.turns.findIndex((turn, i) => i > asked && turn.role === "assistant");
    if (at === -1) at = lastAssistantIndex(feed.turns);
    const card: RunCard = { runId: run.runId, task: run.task, status: run.status, phases: [], result: run.result ?? undefined };
    if (at !== -1) feed = withRun(feed, at, card);
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
    case "agent.update":
      return trackRun(feed, event);
    case "thread":
      // The sidebar shows these.
      return feed;
    case "transcript.delta":
      if (event.role === "user") {
        if (event.itemId !== undefined) return heard(feed, event.itemId, event.text, event.final === true);
        return { seq: feed.seq + 1, turns: [...feed.turns, { role: "user", key: `u${feed.seq}`, text: event.text }] };
      }
      if (event.itemId !== undefined) return answered(feed, event.itemId, event.text, event.final === true);
      return withAssistant(feed, (turn) => ({ ...turn, text: turn.text + event.text }));
    case "widget.render":
      // A thread has one browser: a render of a browser the feed already shows is the server putting it back
      // (this client connected again), so it updates that widget where it is instead of adding another.
      if (event.widget.type === "browser" && hasWidget(feed, event.widget.id)) return updateWidget(feed, event.widget.id, asRecord(event.widget.props));
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
 * An utterance's turn opens (empty, pending) as soon as speech stops, so it sits above the answer
 * even though its transcript usually lands after that answer has started. Deltas append; the
 * final one replaces with the whole transcript, and drops the turn if nothing was said.
 */
function heard(feed: Feed, itemId: string, text: string, final: boolean): Feed {
  const at = feed.turns.findIndex((turn) => turn.role === "user" && turn.itemId === itemId);
  const turn = feed.turns[at];
  if (turn?.role !== "user") {
    if (final && !text) return feed;
    const fresh: UserTurn = { role: "user", key: `u${feed.seq}`, text, itemId, pending: !final };
    return { seq: feed.seq + 1, turns: [...feed.turns, fresh] };
  }
  const full = final ? text : turn.text + text;
  if (final && !full) return { ...feed, turns: feed.turns.filter((_, i) => i !== at) };
  return { ...feed, turns: feed.turns.map((existing, i) => (i === at ? { ...turn, text: full, pending: !final } : existing)) };
}

/**
 * Each answer gets its own bubble, so "let me check" and the result it brings back are two
 * messages. A bubble opened by tool activity before any text is the answer's own.
 */
function answered(feed: Feed, itemId: string, text: string, final: boolean): Feed {
  const at = feed.turns.findIndex((turn) => turn.role === "assistant" && turn.itemId === itemId);
  const turn = feed.turns[at];
  if (turn?.role === "assistant") {
    const full = final ? text : turn.text + text;
    return { ...feed, turns: feed.turns.map((existing, i) => (i === at ? { ...turn, text: full } : existing)) };
  }
  const last = feed.turns[feed.turns.length - 1];
  if (last?.role === "assistant" && last.itemId === undefined && !last.text) {
    return withAssistant(feed, (open) => ({ ...open, itemId, text }));
  }
  const fresh: AssistantTurn = { role: "assistant", key: `a${feed.seq}`, text, itemId, activities: [], widgets: [], runs: [] };
  return { seq: feed.seq + 1, turns: [...feed.turns, fresh] };
}

function hasWidget(feed: Feed, id: string): boolean {
  return feed.turns.some((turn) => turn.role === "assistant" && turn.widgets.some((widget) => widget.id === id));
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
