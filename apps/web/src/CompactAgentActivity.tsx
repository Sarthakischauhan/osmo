import { ApprovalPrompt } from "@/components/ApprovalPrompt";
import type { Runs } from "@/lib/runs";
import { type AgentActivity, type AgentSummary } from "./compact";

export type DecideApproval = (approvalId: string, runId: string, approved: boolean) => Promise<void>;

const STATUS: Record<AgentSummary["status"], string> = {
  running: "working",
  waiting_approval: "needs approval",
  completed: "done",
  failed: "failed",
  cancelled: "cancelled",
};

const DOT: Record<AgentSummary["status"], string> = {
  running: "osmo-dot-warn",
  waiting_approval: "osmo-dot-warn",
  completed: "osmo-dot-ok",
  failed: "osmo-dot-err",
  cancelled: "osmo-dot-none",
};

function counts({ running, needingApproval, elsewhere }: AgentActivity): string {
  const parts: string[] = [];
  if (running > 0) parts.push(needingApproval > 0 ? `${running} active (${needingApproval} waiting for approval)` : `${running} active`);
  if (elsewhere > 0) parts.push(`${elsewhere} in other threads`);
  return parts.join(" · ");
}

/** Each background agent as one summary: its task, where it is, and Approve / Deny when it waits. The full card stays in the workspace. */
export function CompactAgentActivity({ activity, runs, onDecide }: { activity: AgentActivity; runs: Runs; onDecide: DecideApproval }) {
  if (activity.cards.length === 0 && activity.elsewhere === 0) return null;
  const summary = counts(activity);
  return (
    <section className="osmo-compact-agents" aria-label="Agent activity">
      {summary !== "" && <p className="osmo-tertiary osmo-compact-counts">{summary}</p>}
      {activity.cards.map((card) => {
        const { approval } = card;
        return (
        <article key={card.runId} className="osmo-compact-agent" data-status={card.status}>
          <p className="osmo-compact-agent-title">
            <span className={`osmo-dot ${DOT[card.status]}`} aria-hidden />
            <span className="osmo-compact-agent-task">{card.task}</span>
            <span className="osmo-tertiary">{STATUS[card.status]}</span>
          </p>
          {card.detail !== undefined && <p className="osmo-muted osmo-compact-agent-detail">{card.detail}</p>}
          {approval !== undefined && <ApprovalPrompt turn={approval} step={runs[card.runId]?.steps} onDecide={(approved) => void onDecide(approval.approvalId, card.runId, approved)} />}
        </article>
        );
      })}
    </section>
  );
}
