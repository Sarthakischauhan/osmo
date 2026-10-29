import type { AgentActivity } from "./compact";
import type { Runs } from "@/lib/runs";
import { CompactAgentActivity } from "./CompactAgentActivity";
import { CompactTranscript } from "./CompactTranscript";
import type { DecideApproval } from "./ConversationFeed";
import type { Turn } from "./feed";

export interface CompactPanelProps {
  turns: readonly Turn[];
  runs: Runs;
  activity: AgentActivity;
  /** The session's state in words ("Listening", "Speaking"…). */
  status: string;
  theme: "light" | "dark";
  /** The session is live: End is offered. */
  connected: boolean;
  onDecide: DecideApproval;
  onExpand: () => void;
  onEnd: () => void;
}

/**
 * A cropped view of the conversation: header, the latest messages, compact agent summaries and the controls the orb
 * needs. Charts, the browser preview, tool logs and long activity stay in the workspace.
 */
export function CompactPanel({ turns, runs, activity, status, theme, connected, onDecide, onExpand, onEnd }: CompactPanelProps) {
  return (
    <section className="osmo-compact" aria-label="Osmo">
      <header className="osmo-compact-header">
        <strong>Osmo</strong>
        <span className="osmo-tertiary" role="status">
          {status}
        </span>
        <button type="button" className="osmo-btn osmo-btn-ghost osmo-compact-expand" onClick={onExpand}>
          Expand
        </button>
      </header>
      <div className="canvas chat-root osmo-compact-body" data-theme={theme}>
        <CompactTranscript turns={turns} />
        <CompactAgentActivity activity={activity} runs={runs} onDecide={onDecide} />
      </div>
      <footer className="osmo-compact-controls">
        {connected && (
          <button type="button" className="end" onClick={onEnd}>
            <span aria-hidden>✕</span> End
          </button>
        )}
      </footer>
    </section>
  );
}
