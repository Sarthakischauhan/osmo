import type { Runs } from "@/lib/runs";
import { CompactAgentActivity, type DecideApproval } from "./CompactAgentActivity";
import { type AgentActivity, compactMessages } from "./compact";
import type { Turn } from "./feed";

export type OrbVoiceState = "idle" | "listening" | "thinking" | "speaking" | "working";

export interface OrbPreviewProps {
  turns: readonly Turn[];
  runs: Runs;
  activity: AgentActivity;
  onDecide: DecideApproval;
  voiceState?: OrbVoiceState;
  detail?: string;
}

/** One concise, legible transcript in the floating hover panel.
 * Uses existing session deltas: no extra socket or background transcript polling.
 */
export function OrbPreview({ turns, runs, activity, onDecide, voiceState = "idle", detail }: OrbPreviewProps) {
  const messages = compactMessages(turns, 8);
  const current = [...turns].reverse().find((turn) => turn.role === "assistant" && turn.thinking === true);
  const reasoning = current?.role === "assistant" ? current.reasoning?.trim() : undefined;
  const streaming = voiceState === "thinking" || voiceState === "working";
  const progress = detail?.trim() || (voiceState === "working" ? "Running tools" : "Thinking");

  return (
    <>
      <section className="osmo-orb-transcript" aria-label="Live transcript">
        <header className="osmo-orb-panel-head">
          <span className="osmo-orb-panel-label">Conversation</span>
          <span className="osmo-orb-panel-meta" aria-live="off">{streaming ? "Live" : "Recent"}</span>
        </header>
        {messages.length === 0 && !streaming && <p className="osmo-orb-empty">Start speaking to see the transcript here.</p>}
        <div className="osmo-orb-lines" role="log" aria-label="Recent conversation" aria-live="off" aria-relevant="additions text">
          {messages.map((turn) => {
            const assistant = turn.role === "assistant";
            const text = turn.text.trim();
            const active = assistant && turn.thinking === true;
            const activities = assistant ? turn.activities.filter((item) => item.label.trim()) : [];
            const widgets = assistant ? turn.widgets : [];
            // Live reasoning gets its own focused block rather than appearing twice as a transcript line.
            if (assistant && !text && activities.length === 0 && widgets.length === 0) return null;
            return (
              <article key={turn.key} className="osmo-orb-line" data-role={turn.role}>
                <span className="osmo-orb-speaker">{assistant ? "Osmo" : "You"}</span>
                <div className="osmo-orb-entry">
                  {text && <p>{text}</p>}
                  {!assistant && !text && turn.pending && <p className="osmo-orb-pending">Transcribing…</p>}
                  {activities.map((activity, i) => (
                    <p key={i} className="osmo-orb-step">
                      <span className="osmo-orb-step-marker" aria-hidden>{activity.done ? "✓" : "·"}</span>
                      {activity.label}
                    </p>
                  ))}
                  {widgets.map((widget) => (
                    <p key={widget.id} className="osmo-orb-step">
                      {typeof widget.props.title === "string" ? widget.props.title : widget.type}
                      {typeof widget.props.body === "string" ? " · " + widget.props.body : ""}
                    </p>
                  ))}
                  {active && <span className="osmo-orb-stream-dot" aria-label="Still responding" />}
                </div>
              </article>
            );
          })}
        </div>
      </section>
      {streaming && (
        <section className="osmo-orb-live" aria-label="Live assistant activity" aria-live="off">
          <div className="osmo-orb-live-head">
            <span className="osmo-orb-live-indicator" aria-hidden />
            <span>{progress}</span>
            <span className="osmo-orb-live-label">Live</span>
          </div>
          <p className="osmo-orb-live-text">{reasoning || (voiceState === "thinking" ? "Preparing a response…" : "Working on your request…")}<span className="osmo-orb-caret" aria-hidden /></p>
        </section>
      )}
      <CompactAgentActivity activity={activity} runs={runs} onDecide={onDecide} />
    </>
  );
}
