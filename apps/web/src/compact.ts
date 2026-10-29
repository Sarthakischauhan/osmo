import type { AgentRunStatus, OrbMode } from "@osmo/osmo-composer";
import { type ApprovalTurn, type Turn } from "./feed";
import { COMPLETED_LINGER_MS, type RunView, type Runs } from "./lib/runs";

/** What the compact panel shows of a conversation: a cropped view of the same feed and runs, never its own state. */

/** The panel shows the latest 2-3 transcript messages (#18); it shows three. */
export const COMPACT_MESSAGES = 3;

export const ORB_LABELS: Record<OrbMode, string> = {
  connecting: "Connecting",
  idle: "Ready",
  listening: "Listening",
  awaiting: "Your choice",
  thinking: "Thinking",
  speaking: "Speaking",
};

type SpokenTurn = Extract<Turn, { role: "user" | "assistant" }>;

/** The latest spoken lines, oldest first, while a line is still streaming or its transcript is on its way. Tool activity, widgets and approvals are not messages. */
export function compactMessages(turns: readonly Turn[], limit: number = COMPACT_MESSAGES): SpokenTurn[] {
  const spoken = turns.filter((turn): turn is SpokenTurn => (turn.role === "user" && (turn.text !== "" || turn.pending === true)) || (turn.role === "assistant" && turn.text !== ""));
  return spoken.slice(-limit);
}

export interface AgentSummary {
  runId: string;
  task: string;
  status: AgentRunStatus;
  /** The line under the task: the step it is on, its result, or the error. */
  detail: string | undefined;
  /** The approval this run waits on, for Approve / Deny in the panel. */
  approval: ApprovalTurn | undefined;
}

export interface AgentActivity {
  /** This thread's runs, those needing a decision first. */
  cards: AgentSummary[];
  /** Runs of this thread still going, and how many of those wait for approval. */
  running: number;
  needingApproval: number;
  /** Runs of other threads that are still going. */
  elsewhere: number;
}

const PRIORITY: Record<AgentRunStatus, number> = { waiting_approval: 0, running: 1, failed: 2, completed: 3, cancelled: 4 };
const LIVE: ReadonlySet<AgentRunStatus> = new Set(["running", "waiting_approval"]);

function shown(run: RunView, now: number): boolean {
  if (run.status === "completed" || run.status === "cancelled") return run.endedAt !== undefined && now - run.endedAt < COMPLETED_LINGER_MS;
  return true;
}

function detail(run: RunView): string | undefined {
  if (run.status === "completed" || run.status === "failed") return run.result ?? run.phase;
  if (run.status === "cancelled") return "Cancelled";
  return run.phase;
}

/**
 * Agent runs as compact cards: this thread's first (a run whose thread is not known yet is this thread's, as in the feed),
 * a run waiting for approval before one that is working, and a count for the other threads' runs.
 */
export function agentActivity(runs: Runs, turns: readonly Turn[], threadId: string | undefined, now: number): AgentActivity {
  const asking = new Map<string, ApprovalTurn>();
  for (const turn of turns) if (turn.role === "approval" && turn.resolved === undefined) asking.set(turn.runId, turn);
  const all = Object.values(runs);
  const own = all.filter((run) => run.threadId === undefined || run.threadId === threadId);
  const cards = own
    .filter((run) => shown(run, now))
    .sort((a, b) => PRIORITY[a.status] - PRIORITY[b.status] || a.startedAt - b.startedAt)
    .map((run) => ({ runId: run.runId, task: run.task, status: run.status, detail: detail(run), approval: run.status === "waiting_approval" ? asking.get(run.runId) : undefined }));
  return {
    cards,
    running: own.filter((run) => LIVE.has(run.status)).length,
    needingApproval: own.filter((run) => run.status === "waiting_approval").length,
    elsewhere: all.filter((run) => !own.includes(run) && LIVE.has(run.status)).length,
  };
}
