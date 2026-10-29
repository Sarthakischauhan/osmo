import { GlassOrb, type OrbMode } from "@osmo/osmo-composer";
import { useEffect, useRef, useState } from "react";
import { CompactPanel, type CompactPanelProps } from "./CompactPanel";
import { HoverPanel } from "./hoverPanel";

/** Which way the panel opens from the orb: away from the screen edge the orb is nearest. */
export type PanelPlacement = "up-left" | "up-right" | "down-left" | "down-right";

export interface FloatingOrbProps extends CompactPanelProps {
  mode: OrbMode;
  outputLevel: () => number;
  placement: PanelPlacement;
  /** Tap on the orb: start talking, or interrupt while Osmo speaks. */
  onTalk: () => void;
}

/**
 * The floating window's whole content: the orb alone, and the compact panel when the pointer rests on the orb (or an
 * approval waits). It only shows what the session owner passes in; it never opens a connection or a microphone.
 */
export function FloatingOrb({ mode, outputLevel, placement, onTalk, ...panel }: FloatingOrbProps) {
  const [open, setOpen] = useState(false);
  const hover = useRef<HoverPanel<number>>();
  hover.current ??= new HoverPanel<number>({ after: (callback, ms) => window.setTimeout(callback, ms), cancel: (handle) => window.clearTimeout(handle) }, setOpen);
  const waiting = panel.activity.needingApproval > 0;
  useEffect(() => hover.current?.approvalWaiting(waiting), [waiting]);
  useEffect(() => () => hover.current?.dispose(), []);
  const track = (region: "orb" | "panel") => ({
    onPointerEnter: () => hover.current?.enter(region),
    onPointerLeave: () => hover.current?.leave(region),
  });

  return (
    <div className="osmo-floating" data-open={open} data-placement={placement} data-theme={panel.theme}>
      {open && (
        <div className="osmo-floating-panel" {...track("panel")} onPointerDown={() => hover.current?.pin()}>
          <CompactPanel {...panel} />
        </div>
      )}
      <button type="button" className="osmo-floating-orb" data-mode={mode} aria-label={mode === "speaking" ? "Interrupt" : "Talk to Osmo"} onClick={onTalk} {...track("orb")}>
        <GlassOrb mode={mode} outputLevel={outputLevel} />
      </button>
    </div>
  );
}
