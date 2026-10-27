import type { CSSProperties } from "react";
import { GlassOrb } from "./GlassOrb";
import { orbMode } from "./orbVisual";
import type { OsmoState } from "./protocol";
import { RadialPicker, type RadialPickerProps } from "./radial-picker/RadialPicker";
import type { OsmoSession } from "./useOsmoSession";

const CAPTIONS: Record<OsmoState, string> = {
  idle: "Tap the orb to talk",
  listening: "Listening · just talk",
  thinking: "Thinking…",
  working: "Working…",
  speaking: "Speaking · tap to interrupt · or just talk over it",
};

export interface OrbComposerProps {
  session: OsmoSession;
  /** An options list or action card is waiting on the user: the orb turns amber. */
  awaitingChoice?: boolean;
  className?: string;
  style?: CSSProperties;
  /**
   * Wrap the orb in the radial voice picker: hovering or focusing the orb opens the
   * dial, a tap still starts or interrupts the conversation.
   */
  picker?: Omit<RadialPickerProps, "orb" | "orbSize" | "onOrbActivate" | "orbLabel">;
  /** Why the picker's models could not be loaded (shown while the app retries). */
  pickerError?: string;
}

/**
 * The orb: the only input surface. Tap to connect, tap while speaking to
 * interrupt. Shaped as a chat SDK composer so it can drop into that slot later.
 */
export function OrbComposer({ session, awaitingChoice = false, className, style, picker, pickerError }: OrbComposerProps) {
  const { connection, state, detail, error, outputLevel } = session;
  const shown: OsmoState = connection === "connected" ? state : "idle";
  const mode = orbMode(connection, shown, awaitingChoice);
  const caption =
    connection === "connecting"
      ? "Connecting…"
      : (error ?? pickerError ?? detail ?? (mode === "awaiting" ? "Waiting for your choice" : CAPTIONS[shown]));

  const onTap = () => {
    if (connection === "disconnected") void session.start();
    else if (shown === "speaking") session.interrupt();
  };
  const label = shown === "speaking" ? "Interrupt" : connection === "disconnected" ? "Start talking" : "Orb";
  const orb = <GlassOrb mode={mode} outputLevel={outputLevel} />;

  return (
    <div className={`orb-composer ${className ?? ""}`} style={style} data-state={shown} data-mode={mode} data-connection={connection}>
      {picker ? (
        <RadialPicker {...picker} orb={orb} orbSize={64} onOrbActivate={onTap} orbLabel={label} />
      ) : (
        <button type="button" className="orb" data-state={shown} data-mode={mode} data-connection={connection} aria-label={label} onClick={onTap}>
          {orb}
        </button>
      )}
      <p className="orb-caption" role="status" aria-live="polite">
        {caption}
      </p>
    </div>
  );
}
