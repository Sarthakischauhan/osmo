import type { CSSProperties, ReactNode } from "react";
import type { RingKey } from "./geometry";

/**
 * The one option primitive used by every ring (providers, models, voices, effort): same height,
 * padding, radius, gap and font everywhere, set only by the `--rp-option-*` tokens on `.rp`
 * (see README "Option tokens"). No ring has its own size rules.
 */
export type OptionState = "idle" | "hover" | "selected";

export interface RadialOptionProps {
  id: string;
  ring: RingKey;
  state: OptionState;
  selected: boolean;
  /** Accessible name (also the native tooltip unless the option draws its own `tip`). */
  label: string;
  title?: string;
  /** A locked provider opens its connect dialog; a locked model (transcription only) does nothing. */
  locked?: boolean;
  delayMs: number;
  onCommit: () => void;
  children: ReactNode;
}

export function RadialOption({ id, ring, state, selected, label, title, locked, delayMs, onCommit, children }: RadialOptionProps) {
  const style: CSSProperties = { animationDelay: `${delayMs}ms` };
  return (
    <div
      id={id}
      role="option"
      aria-selected={selected}
      aria-label={label}
      aria-haspopup={locked && ring === "providers" ? "dialog" : undefined}
      aria-disabled={locked && ring !== "providers" ? true : undefined}
      title={title}
      className="rp-option"
      data-ring={ring}
      data-locked={locked ? true : undefined}
      data-state={state}
      style={style}
      onPointerDown={(event) => event.preventDefault()}
      onClick={onCommit}
    >
      {children}
    </div>
  );
}
