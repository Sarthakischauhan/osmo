import type { CSSProperties } from "react";
import type { OrbState } from "./protocol";
import type { OrbSession } from "./useOrbSession";

const CAPTIONS: Record<OrbState, string> = {
  idle: "Tap the orb to talk",
  listening: "Listening · just talk",
  thinking: "Thinking…",
  working: "Working…",
  speaking: "Speaking · tap to interrupt · or just talk over it",
};

export interface OrbComposerProps {
  session: OrbSession;
  className?: string;
  style?: CSSProperties;
}

/**
 * The orb: the only input surface. Tap to connect, tap while speaking to
 * interrupt. Shaped as a chat SDK composer so it can drop into that slot later.
 */
export function OrbComposer({ session, className, style }: OrbComposerProps) {
  const { connection, state, detail, error } = session;
  const shown: OrbState = connection === "connected" ? state : "idle";
  const caption =
    connection === "connecting"
      ? "Connecting…"
      : (error ?? detail ?? CAPTIONS[shown]);

  const onTap = () => {
    if (connection === "disconnected") void session.start();
    else if (shown === "speaking") session.interrupt();
  };

  return (
    <div className={`orb-composer ${className ?? ""}`} style={style} data-state={shown}>
      <button
        type="button"
        className="orb"
        data-state={shown}
        aria-label={shown === "speaking" ? "Interrupt" : connection === "disconnected" ? "Start talking" : "Orb"}
        onClick={onTap}
      >
        <span className="orb-halo" />
        <span className="orb-sphere" />
      </button>
      <p className="orb-caption" role="status" aria-live="polite">
        {caption}
      </p>
    </div>
  );
}
