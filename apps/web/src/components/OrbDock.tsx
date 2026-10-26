import { type OrbMode, type OsmoSession } from "@osmo/osmo-composer";
import { ThinkingBlock } from "@sarchauhan/chat";
import { useState } from "react";

export interface OrbDockProps {
  session: OsmoSession;
  mode: OrbMode;
  /** What the voice model is doing now, e.g. "checking forecast". */
  thinking: string | undefined;
  /** The current turn's tool steps, for the expanded thinking popover. */
  steps: readonly string[];
}

const CAPTIONS: Record<OrbMode, string> = {
  connecting: "Connecting…",
  idle: "Tap to talk",
  listening: "Listening",
  awaiting: "Waiting for your choice",
  thinking: "Thinking",
  speaking: "Speaking · tap to interrupt",
};

/**
 * The orb as osmo-design specifies it (solid `--foreground`, states by scale and opacity, a thinking
 * popover above it), behind `VITE_SDK_ORB`. It takes the place of chat-sdk#43's
 * `ChatComposer collapsedVariant="circle"` until that ships in @sarchauhan/chat 0.1.0: 0.0.2's
 * composer is a text input with no voice states, so only its ThinkingBlock is used here.
 */
export function OrbDock({ session, mode, thinking, steps }: OrbDockProps) {
  const [expanded, setExpanded] = useState(false);
  const { connection, state } = session;
  const onTap = () => {
    if (connection === "disconnected") void session.start();
    else if (state === "speaking") session.interrupt();
  };
  const label = state === "speaking" ? "Interrupt" : connection === "disconnected" ? "Start talking" : "Orb";
  const showPopover = mode === "thinking";
  return (
    <div className="osmo-orb-dock" data-mode={mode}>
      {showPopover && (
        <div className="osmo-thinking" role="status">
          <button type="button" className="osmo-thinking-summary" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
            {thinking ? `Thinking · ${thinking}` : steps.length > 0 ? `Thinking · ${steps.length} ${steps.length === 1 ? "step" : "steps"} ▾` : "Thinking…"}
          </button>
          {expanded && steps.length > 0 && (
            <ThinkingBlock isComplete={false}>
              <ol className="osmo-thinking-steps">
                {steps.map((step, i) => (
                  <li key={`${i}-${step}`}>{step}</li>
                ))}
              </ol>
            </ThinkingBlock>
          )}
        </div>
      )}
      <button type="button" className="osmo-orb" data-mode={mode} aria-label={label} onClick={onTap} />
      <p className="osmo-orb-caption" role="status" aria-live="polite">
        {session.error ?? CAPTIONS[mode]}
      </p>
    </div>
  );
}
