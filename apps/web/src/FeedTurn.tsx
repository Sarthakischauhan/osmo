import { toWidgetPart } from "@osmo/osmo-widgets";
import { MessageContent } from "@sarchauhan/chat";
import type { AgentPart, AgentSubagent, AgentSubagentState } from "@sarchauhan/protocol";
import { ApprovalPrompt } from "@/components/ApprovalPrompt";
import type { DecideApproval } from "./CompactAgentActivity";
import type { RunCard, Turn } from "./feed";

type AssistantTurn = Extract<Turn, { role: "assistant" }>;

export interface FeedTurnProps {
  turn: Turn;
  /** The step count of the run an approval belongs to. */
  step: number | undefined;
  onDecide: DecideApproval;
  /** The orb's preview: the spoken line only. Tool activity, run cards and widgets belong to the workspace. */
  compact?: boolean;
}

/** One turn of the conversation as chat SDK messages: the single place a feed is rendered, for the workspace and the orb's preview. */
export function FeedTurn({ turn, step, onDecide, compact = false }: FeedTurnProps) {
  switch (turn.role) {
    case "user":
      return (
        <div className="turn user">
          <div className="bubble" data-pending={turn.pending || undefined}>
            {turn.text ? (
              <MessageContent parts={[{ type: "text", text: turn.text }]} isUser />
            ) : (
              <span className="heard-dots" aria-label="Transcribing">
                <i />
                <i />
                <i />
              </span>
            )}
          </div>
        </div>
      );
    case "approval":
      return <ApprovalPrompt turn={turn} step={step} onDecide={(approved) => void onDecide(turn.approvalId, turn.runId, approved)} />;
    case "assistant":
      return (
        <div className="turn assistant">
          <span className="avatar" aria-hidden />
          <div className="assistant-body">
            <MessageContent parts={assistantParts(turn, compact)} />
            {/* One element per widget keyed by its server id, so React state follows the
                widget itself and never shifts when text or activity rows are added above. */}
            {!compact &&
              turn.widgets.map((widget) => {
                const part = toWidgetPart(widget.id, widget.type, widget.props);
                return part ? <MessageContent key={widget.id} parts={[part]} /> : null;
              })}
          </div>
        </div>
      );
  }
}


/** One assistant turn's spoken line, tool activity rows and agent runs as chat-sdk parts; widgets render separately, keyed by id. */
function assistantParts(turn: AssistantTurn, compact: boolean): AgentPart[] {
  const parts: AgentPart[] = [];
  if (turn.text) parts.push({ type: "text", text: turn.text });
  if (compact) return parts;
  turn.activities.forEach((activity, i) =>
    parts.push({
      type: "tool",
      toolName: "activity",
      toolCallId: `${turn.key}-tool-${i}`,
      title: activity.label,
      state: activity.done ? "output-available" : "input-available",
    }),
  );
  for (const run of turn.runs) parts.push({ type: "data", name: "subagent", id: run.runId, data: subagent(run) });
  return parts;
}

const SUBAGENT_STATE: Record<RunCard["status"], AgentSubagentState> = {
  running: "running",
  waiting_approval: "running",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

function subagent(run: RunCard): AgentSubagent {
  return {
    id: run.runId,
    title: "Symphony",
    task: run.task,
    state: SUBAGENT_STATE[run.status],
    error: run.status === "failed" ? run.result : undefined,
    // The answer is the voice model's to tell; the card keeps it under View activity, after the steps.
    messages: [...run.phases, ...(run.status === "completed" && run.result ? [run.result] : [])].map((text, i) => ({
      id: `${run.runId}-${i}`,
      role: "assistant",
      parts: [{ type: "text", text }],
    })),
  };
}
