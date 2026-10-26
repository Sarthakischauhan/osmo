import type { AgentRunStatus, ServerEvent } from "@osmo/osmo-composer";
import { z } from "zod";
import { type AgentRun, agentRunSchema } from "./threads";

/** Background agent runs (Symphony, via `run_agent`) as the home screen's Running list shows them. */
export interface RunView {
  runId: string;
  /** The thread that started it; known once `GET /api/runs` lists it. */
  threadId: string | undefined;
  task: string;
  status: AgentRunStatus;
  /** The latest step the agent started (its tool activity), if any. */
  phase: string | undefined;
  /** How many steps it has started so far. */
  steps: number;
  result: string | undefined;
  startedAt: number;
  /** When it finished (completed, failed or cancelled), ms. */
  endedAt: number | undefined;
  approvalId: string | undefined;
}

export type Runs = Record<string, RunView>;

/** Completed runs stay in Running this long before they move to Recent. */
export const COMPLETED_LINGER_MS = 120_000;

const FINISHED: ReadonlySet<AgentRunStatus> = new Set(["completed", "failed", "cancelled"]);

export type ApprovalRequest = Extract<ServerEvent, { type: "approval.request" }>;

const approvalRequestSchema = z.object({
  type: z.literal("approval.request"),
  approvalId: z.string(),
  runId: z.string(),
  tool: z.string(),
  summary: z.string(),
  arguments: z.string(),
});
const runListSchema = z.object({ runs: z.array(agentRunSchema), approvals: z.array(approvalRequestSchema) });
export type RunList = z.infer<typeof runListSchema>;

/** Every thread's live runs, and those finished since `since` (ms), from `GET /api/runs`. */
export async function fetchRuns(since: number, fetcher: typeof fetch = fetch): Promise<RunList> {
  const response = await fetcher(`/api/runs?since=${since / 1000}`);
  if (!response.ok) throw new Error(`GET /api/runs answered ${response.status}`);
  return runListSchema.parse(await response.json());
}

/** The server's list folded into what the socket's events built (which know the step count). */
export function mergeListed(runs: Runs, listed: RunList): Runs {
  const asking = new Map(listed.approvals.map((approval) => [approval.runId, approval.approvalId]));
  const next: Runs = { ...runs };
  for (const run of listed.runs) next[run.runId] = listedView(runs[run.runId], run, asking.get(run.runId));
  return next;
}

function listedView(previous: RunView | undefined, run: AgentRun, approvalId: string | undefined): RunView {
  return {
    runId: run.runId,
    threadId: run.threadId,
    task: run.task,
    status: run.status,
    phase: previous?.phase,
    steps: previous?.steps ?? 0,
    result: run.result ?? previous?.result,
    startedAt: run.createdAt * 1000,
    endedAt: FINISHED.has(run.status) ? run.updatedAt * 1000 : undefined,
    approvalId,
  };
}

export function reduceRuns(runs: Runs, event: ServerEvent, now: number): Runs {
  switch (event.type) {
    case "agent.update": {
      const previous = runs[event.runId];
      const phase = event.phase ?? undefined;
      // The server reports each tool the agent starts as a running update with its phase.
      const newStep = phase !== undefined && event.status === "running";
      const next: RunView = {
        runId: event.runId,
        threadId: previous?.threadId,
        task: event.task,
        status: event.status,
        phase: phase ?? previous?.phase,
        steps: (previous?.steps ?? 0) + (newStep ? 1 : 0),
        result: event.result ?? previous?.result,
        startedAt: previous?.startedAt ?? now,
        endedAt: FINISHED.has(event.status) ? (previous?.endedAt ?? now) : undefined,
        approvalId: event.status === "waiting_approval" ? previous?.approvalId : undefined,
      };
      return { ...runs, [event.runId]: next };
    }
    case "approval.request": {
      const run = runs[event.runId];
      return run ? { ...runs, [event.runId]: { ...run, status: "waiting_approval", approvalId: event.approvalId } } : runs;
    }
    case "approval.resolved": {
      const run = runs[event.runId];
      return run?.approvalId === event.approvalId ? { ...runs, [event.runId]: { ...run, approvalId: undefined } } : runs;
    }
    default:
      return runs;
  }
}

/** The Running list: live runs, failures, and completed runs for two minutes after they finish. */
export function visibleRuns(runs: Runs, now: number): RunView[] {
  return Object.values(runs)
    .filter((run) => {
      if (run.status === "cancelled") return false;
      if (run.status === "completed") return run.endedAt !== undefined && now - run.endedAt < COMPLETED_LINGER_MS;
      return true;
    })
    .sort((a, b) => a.startedAt - b.startedAt);
}

/** mm:ss since `from` (to `to`). */
export function elapsed(from: number, to: number): string {
  const seconds = Math.max(0, Math.floor((to - from) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export async function decideApproval(approvalId: string, approved: boolean, fetcher: typeof fetch = fetch): Promise<boolean> {
  const response = await fetcher(`/api/approvals/${encodeURIComponent(approvalId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
  return response.ok;
}
