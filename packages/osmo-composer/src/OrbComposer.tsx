import { type CSSProperties, useEffect, useRef } from "react";
import { type OrbMode, drawOrb, initialFrame, orbMode, step } from "./orbVisual";
import type { OsmoState } from "./protocol";
import type { OsmoSession } from "./useOsmoSession";

const CAPTIONS: Record<OsmoState, string> = {
  idle: "Tap the orb to talk",
  listening: "Listening · just talk",
  thinking: "Thinking…",
  working: "Working…",
  speaking: "Speaking · tap to interrupt · or just talk over it",
};

/** Canvas size in CSS pixels; larger than the 64px button so the glow has room. */
const CANVAS = 140;

export interface OrbComposerProps {
  session: OsmoSession;
  /** An options list or action card is waiting on the user: the orb turns amber. */
  awaitingChoice?: boolean;
  className?: string;
  style?: CSSProperties;
}

/**
 * The orb: the only input surface. Tap to connect, tap while speaking to
 * interrupt. Shaped as a chat SDK composer so it can drop into that slot later.
 */
export function OrbComposer({ session, awaitingChoice = false, className, style }: OrbComposerProps) {
  const { connection, state, detail, error, outputLevel } = session;
  const shown: OsmoState = connection === "connected" ? state : "idle";
  const mode = orbMode(connection, shown, awaitingChoice);
  const caption =
    connection === "connecting"
      ? "Connecting…"
      : (error ?? detail ?? (mode === "awaiting" ? "Waiting for your choice" : CAPTIONS[shown]));

  const canvas = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef<OrbMode>(mode);
  modeRef.current = mode;

  // One rAF loop for the component's lifetime; it reads mode and level through refs, so audio never re-renders React.
  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext("2d");
    if (!element || !ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const size = Math.round(CANVAS * dpr);
    element.width = size;
    element.height = size;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const frame = initialFrame(modeRef.current);
    let last = performance.now();
    let handle = 0;
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const level = outputLevel();
      step(frame, modeRef.current, motion.matches ? 0 : level, motion.matches ? 1 : dt, motion.matches);
      drawOrb(ctx, size, frame, modeRef.current);
      element.dataset.level = frame.level.toFixed(2);
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(handle);
  }, [outputLevel]);

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
        data-mode={mode}
        data-connection={connection}
        aria-label={shown === "speaking" ? "Interrupt" : connection === "disconnected" ? "Start talking" : "Orb"}
        onClick={onTap}
      >
        <canvas ref={canvas} className="orb-canvas" aria-hidden style={{ width: CANVAS, height: CANVAS }} />
      </button>
      <p className="orb-caption" role="status" aria-live="polite">
        {caption}
      </p>
    </div>
  );
}
