import type { ApprovalTurn } from "../feed";

export interface ApprovalPromptProps {
  turn: ApprovalTurn;
  /** The step of its run this approval is for (the run's started steps so far); 0 when the run was only listed by the server, which does not count them. */
  step: number | undefined;
  onDecide: (approved: boolean) => void;
}

const TIME = { hour: "numeric", minute: "2-digit" } as const;

/** A background agent's risky step, in the feed: Approve / Deny, then one line once answered. */
export function ApprovalPrompt({ turn, step, onDecide }: ApprovalPromptProps) {
  if (turn.resolved) {
    const time = new Date(turn.resolved.at).toLocaleTimeString("en-US", TIME);
    return (
      <p className="osmo-approval-resolved" role="status">
        {turn.resolved.approved ? `✓ Approved · ${time}` : `✕ Denied · ${time}`}
      </p>
    );
  }
  return (
    <div id={`approval-${turn.approvalId}`} className="osmo-approval" role="group" aria-live="polite" aria-label="Approval needed">
      <p className="osmo-approval-title">{step === undefined || step === 0 ? `Approve ${turn.summary.toLowerCase()}?` : `Approve step ${step}?`}</p>
      <p className="osmo-approval-call">
        {turn.tool} {turn.arguments}
      </p>
      <div className="osmo-approval-actions">
        <button type="button" className="osmo-btn osmo-btn-primary" onClick={() => onDecide(true)}>
          Approve
        </button>
        <button type="button" className="osmo-btn osmo-btn-ghost" onClick={() => onDecide(false)}>
          Deny
        </button>
        {/* Approvals are taken on screen only: the voice model never approves for the user. */}
        <span className="osmo-approval-hint">Tap Approve or Deny</span>
      </div>
    </div>
  );
}
