import { useEffect, useRef } from "react";
import { type OrbMode, drawOrb, initialFrame, step } from "./orbVisual";

/** Canvas size in CSS pixels; larger than the 64px button so the wobble has room. */
export const ORB_CANVAS = 140;

export interface GlassOrbProps {
  mode: OrbMode;
  /** Current playback level, 0..1; read every frame. */
  outputLevel: () => number;
}

/**
 * The orb: a solid `--foreground` canvas blob redrawn every frame from `mode` and the
 * playback level. It draws only; the parent owns the button and what a tap does.
 */
export function GlassOrb({ mode, outputLevel }: GlassOrbProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef<OrbMode>(mode);
  modeRef.current = mode;

  // One rAF loop for the component's lifetime; it reads mode and level through refs, so audio never re-renders React.
  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext("2d");
    if (!element || !ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const size = Math.round(ORB_CANVAS * dpr);
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

  return <canvas ref={canvas} className="orb-canvas" aria-hidden style={{ width: ORB_CANVAS, height: ORB_CANVAS }} />;
}
