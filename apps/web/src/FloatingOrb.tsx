import { GlassOrb, type OrbMode } from "@osmo/osmo-composer";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { BoxPlacement } from "./desktopBridge";
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
  /** Double-click on the orb: back to the full window. */
  onExpand: () => void;
  /** The window's room for the box: `placed` is the box height it was last given (0 until it has answered). */
  placement: BoxPlacement & { placed: number };
  /** The box's content is now `height` tall (0 when there is no box); the window is resized to fit and `placement` updated. */
  onBoxHeight: (height: number) => void;
}

/**
 * The floating window's whole content: the orb, and beside it (below, or above when the window had no room below) the
 * agent log while the pointer rests on the orb or an approval waits. It only shows what the session owner passes in;
 * it never opens a connection or a microphone.
 */
export function FloatingOrb({ mode, outputLevel, runs, activity, theme, onDecide, onTalk, onExpand, placement, onBoxHeight }: FloatingOrbProps) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const hover = useRef<HoverPanel<number>>();
  hover.current ??= new HoverPanel<number>({ after: (callback, ms) => window.setTimeout(callback, ms), cancel: (handle) => window.clearTimeout(handle) }, setOpen);
  const waiting = activity.needingApproval > 0;
  useEffect(() => hover.current?.approvalWaiting(waiting), [waiting]);
  useEffect(() => () => hover.current?.dispose(), []);
  // The window is hidden while the full window shows: forget the pointer, so the box starts closed when it comes back.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "hidden") hover.current?.reset();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);
  // The box reports its own height, so the window is exactly as tall as it needs.
  const hasBox = open && (activity.cards.length > 0 || activity.elsewhere > 0);
  useLayoutEffect(() => {
    const element = box.current;
    if (!hasBox || !element) {
      onBoxHeight(0);
      return;
    }
    onBoxHeight(element.offsetHeight);
    const observer = new ResizeObserver(() => onBoxHeight(element.offsetHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasBox, onBoxHeight]);
  const track = (region: "orb" | "panel") => ({
    onPointerEnter: () => hover.current?.enter(region),
    onPointerLeave: () => hover.current?.leave(region),
  });

  return (
    <div className="osmo-floating" data-theme={theme} data-side={placement.side} style={{ ["--orb-left" as string]: `${placement.orbLeft}px` }}>
      <button
        type="button"
        className="osmo-floating-orb"
        data-mode={mode}
        aria-label={mode === "speaking" ? "Interrupt" : "Talk to Osmo"}
        onClick={onTalk}
        onDoubleClick={onExpand}
        {...track("orb")}
      >
        <GlassOrb mode={mode} outputLevel={outputLevel} />
      </button>
      {open && (
        <div ref={box} className="osmo-floating-box" data-placed={placement.placed > 0 || undefined} {...track("panel")} onPointerDown={() => hover.current?.pin()}>
          <CompactAgentActivity activity={activity} runs={runs} onDecide={onDecide} />
        </div>
      )}
    </div>
  );
}
