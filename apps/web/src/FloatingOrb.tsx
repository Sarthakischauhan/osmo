import { GlassOrb, type OrbMode } from "@osmo/osmo-composer";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { BoxPlacement } from "./desktopBridge";
import type { Runs } from "@/lib/runs";
import type { DecideApproval } from "./CompactAgentActivity";
import { type AgentActivity, hasPreview } from "./compact";
import type { Turn } from "./feed";
import type { OrbVoiceState } from "./OrbPreview";
import { OrbPreview } from "./OrbPreview";
import { HoverPanel, type Scheduler } from "./hoverPanel";
import { OrbDrag } from "./orbDrag";
import { SingleClick } from "./singleClick";

const windowTimers: Scheduler<number> = { after: (callback, ms) => window.setTimeout(callback, ms), cancel: (handle) => window.clearTimeout(handle) };

export interface FloatingOrbProps {
  mode: OrbMode;
  voiceState?: OrbVoiceState;
  detail?: string;
  outputLevel: () => number;
  /** Mic level 0..1, so the floating orb reacts while the user talks. */
  inputLevel?: () => number;
  runs: Runs;
  /** The conversation so far; the box shows its latest spoken lines. */
  turns: readonly Turn[];
  activity: AgentActivity;
  theme: "light" | "dark";
  onDecide: DecideApproval;
  /** Tap on the orb, once it is clear it was not the first half of a double-click: start talking, or interrupt while Osmo speaks. */
  onTalk: () => void;
  /** Double-click on the orb: back to the full window. */
  onExpand: () => void;
  /** The window's room for the box: `placed` is the box height it was last given (0 until it has answered). */
  placement: BoxPlacement & { placed: number };
  /** The box's content is now `height` tall (0 when there is no box); the window is resized to fit and `placement` updated. */
  onBoxHeight: (height: number) => void;
  /** A drag of the orb moved the pointer (`dx`, `dy`) screen px: move the window with it. */
  onMove?: (dx: number, dy: number) => void;
}

/**
 * The floating window's whole content: the orb, and beside it (below, or above when the window had no room below) the
 * preview box while the pointer rests on the orb or an approval waits: the latest spoken lines, then the agent log
 * with its approvals. It only shows what the session owner passes in;
 * it never opens a connection or a microphone.
 */
export function FloatingOrb({ mode, voiceState, detail, outputLevel, inputLevel, runs, turns, activity, theme, onDecide, onTalk, onExpand, placement, onBoxHeight, onMove }: FloatingOrbProps) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const hover = useRef<HoverPanel<number>>();
  hover.current ??= new HoverPanel<number>(windowTimers, setOpen);
  const talk = useRef(onTalk);
  talk.current = onTalk;
  const tap = useRef<SingleClick<number>>();
  tap.current ??= new SingleClick<number>(windowTimers, () => talk.current());
  const move = useRef(onMove);
  move.current = onMove;
  const drag = useRef<OrbDrag>();
  drag.current ??= new OrbDrag((dx, dy) => move.current?.(dx, dy));
  const [dragging, setDragging] = useState(false);
  const waiting = activity.needingApproval > 0;
  useEffect(() => hover.current?.approvalWaiting(waiting), [waiting]);
  useEffect(() => () => hover.current?.dispose(), []);
  useEffect(() => () => tap.current?.cancel(), []);
  // The window is hidden while the full window shows: forget the pointer, so the box starts closed when it comes back.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "hidden") hover.current?.reset();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);
  // The box reports its own height, so the window is exactly as tall as it needs.
  const hasBox = open && (hasPreview(turns, activity) || voiceState === "thinking" || voiceState === "working");
  useLayoutEffect(() => {
    const element = box.current;
    if (!hasBox || !element) {
      onBoxHeight(0);
      return;
    }
    const report = () => {
      onBoxHeight(Math.min(400, element.offsetHeight));
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasBox, onBoxHeight, turns, activity, voiceState, detail]);
  // Follow live text only when already near the bottom. Do not steal the scroll position
  // while someone is reviewing older messages in the hover transcript.
  const latest = turns[turns.length - 1];
  const liveText = latest?.role === "assistant" ? (latest.text + (latest.reasoning ?? "")) : latest?.role === "user" ? latest.text : "";
  useEffect(() => {
    const element = box.current;
    if (!hasBox || !element) return;
    if (element.scrollHeight - element.scrollTop - element.clientHeight < 64) element.scrollTop = element.scrollHeight;
  }, [hasBox, turns.length, liveText]);
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
        data-dragging={dragging || undefined}
        onPointerDown={(event) => {
          if (event.button !== 0 || !onMove) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current?.down(event.screenX, event.screenY);
        }}
        onPointerMove={(event) => {
          if (drag.current?.moveTo(event.screenX, event.screenY) && !dragging) setDragging(true);
        }}
        onPointerUp={() => {
          drag.current?.up();
          setDragging(false);
        }}
        onPointerCancel={() => {
          drag.current?.up();
          setDragging(false);
        }}
        onClick={() => {
          if (drag.current?.takeClick() !== false) tap.current?.click();
        }}
        onDoubleClick={() => {
          tap.current?.cancel();
          onExpand();
        }}
        {...track("orb")}
      >
        <GlassOrb mode={mode} outputLevel={outputLevel} inputLevel={inputLevel} />
      </button>
      {open && (
        <div ref={box} className="osmo-floating-box osmo-scroll" data-placed={placement.placed > 0 || undefined} {...track("panel")} onPointerDown={() => hover.current?.pin()}>
          <OrbPreview turns={turns} runs={runs} activity={activity} onDecide={onDecide} voiceState={voiceState} detail={detail} />
        </div>
      )}
    </div>
  );
}
