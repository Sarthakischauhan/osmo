import { osmoWidgets } from "@osmo/osmo-widgets";
import { WidgetProvider } from "@sarchauhan/chat";
import type { Runs } from "@/lib/runs";
import { CompactAgentActivity, type DecideApproval } from "./CompactAgentActivity";
import { type AgentActivity, compactMessages } from "./compact";
import type { Turn } from "./feed";
import { FeedTurn } from "./FeedTurn";

export interface OrbPreviewProps {
  turns: readonly Turn[];
  runs: Runs;
  activity: AgentActivity;
  onDecide: DecideApproval;
}

/**
 * What the orb's preview box holds: the latest spoken lines as the workspace renders them, then the agent log, whose
 * entries carry Approve / Deny. Tool activity, widgets and run cards stay in the workspace.
 */
export function OrbPreview({ turns, runs, activity, onDecide }: OrbPreviewProps) {
  const messages = compactMessages(turns, Number.POSITIVE_INFINITY);
  return (
    <>
      {messages.length > 0 && (
        <WidgetProvider widgets={osmoWidgets} respondToWidget={async () => undefined} disabled>
          <div className="canvas chat-root osmo-compact-transcript" role="log" aria-label="Conversation">
            {messages.map((turn) => (
              <FeedTurn key={turn.key} turn={turn.role === "assistant" ? { ...turn, runs: [] } : turn} step={undefined} onDecide={onDecide} />
            ))}
          </div>
        </WidgetProvider>
      )}
      <CompactAgentActivity activity={activity} runs={runs} onDecide={onDecide} />
    </>
  );
}
