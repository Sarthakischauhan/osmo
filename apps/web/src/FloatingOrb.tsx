import { GlassOrb, type OrbMode } from "@osmo/osmo-composer";
import { useEffect, useRef, useState } from "react";
import type { Runs } from "@/lib/runs";
import { CompactAgentActivity, type DecideApproval } from "./CompactAgentActivity";
import type { AgentActivity } from "./compact";
import { HoverPanel } from "./hoverPanel";

export interface FloatingOrbProps {
  mode: OrbMode;
  outputLevel: () => number;
  runs: Runs;
  activity: AgentActivity;
  theme: "light" | "dark";
  onDecide: DecideApproval;
  /** Tap on the orb: start talking, or interrupt while Osmo speaks. */
  onTalk: () => void;
}

/**
 * The floating window's whole content: the orb, and below it the agent log while the pointer rests on the orb (or an
 * approval waits). It only shows what the session owner passes in; it never opens a connection or a microphone.
 */
export function FloatingOrb({ mode, outputLevel, runs, activity, theme, onDecide, onTalk }: FloatingOrbProps) {
  const [open, setOpen] = useState(false);
  const hover = useRef<HoverPanel<number>>();
  hover.current ??= new HoverPanel<number>({ after: (callback, ms) => window.setTimeout(callback, ms), cancel: (handle) => window.clearTimeout(handle) }, setOpen);
  const waiting = activity.needingApproval > 0;
  useEffect(() => hover.current?.approvalWaiting(waiting), [waiting]);
  useEffect(() => () => hover.current?.dispose(), []);
  const track = (region: "orb" | "panel") => ({
    onPointerEnter: () => hover.current?.enter(region),
    onPointerLeave: () => hover.current?.leave(region),
  });

  return (
    <div className="osmo-floating" data-theme={theme}>
      <button type="button" className="osmo-floating-orb" data-mode={mode} aria-label={mode === "speaking" ? "Interrupt" : "Talk to Osmo"} onClick={onTalk} {...track("orb")}>
        <GlassOrb mode={mode} outputLevel={outputLevel} />
      </button>
      {open && (
        <div {...track("panel")} onPointerDown={() => hover.current?.pin()}>
          <CompactAgentActivity activity={activity} runs={runs} onDecide={onDecide} />
        </div>
      )}
    </div>
  );
}
