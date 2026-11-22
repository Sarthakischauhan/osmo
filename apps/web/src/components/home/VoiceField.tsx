import type { OrbMode } from "@osmo/osmo-composer";
import { useEffect, useRef } from "react";

/** Grid pitch, ripple speed (px/s) and ring width, in CSS pixels. */
const PITCH = 20;
const SPEED = 150;
const RING = 26;
/** Dots inside this radius stay clear so the orb reads on its own. */
const ORB_CLEARANCE = 48;

interface Ripple {
  born: number;
  amp: number;
}

export interface VoiceFieldProps {
  mode: OrbMode;
  /** Mic level 0..1, read every frame. */
  inputLevel: () => number;
  /** Playback level 0..1, read every frame. */
  outputLevel: () => number;
}

/**
 * The welcome screen's backdrop: a dot grid centred on the dock orb that ripples with the
 * session. Idle breathes, listening and speaking ripple with the live level, thinking sweeps.
 * Drawn in the element's `color` (the theme foreground); static under reduced motion.
 */
export function VoiceField({ mode, inputLevel, outputLevel }: VoiceFieldProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let width = 0;
    let height = 0;
    let dpr = 1;
    let color = "";
    let frame = 0;
    let handle = 0;
    let last = performance.now() / 1000;
    let lastSpawn = 0;
    let lastPulse = 0;
    let sweep = 0;
    let level = 0;
    let ripples: Ripple[] = [];

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    /** The orb's centre in the canvas, wherever the layout has put it. */
    const orbCentre = (): [number, number] => {
      const orb = document.querySelector(".dock .rp-orb, .dock .orb");
      const box = canvas.getBoundingClientRect();
      if (!orb) return [width / 2, height * 0.6];
      const rect = orb.getBoundingClientRect();
      return [rect.left + rect.width / 2 - box.left, rect.top + rect.height / 2 - box.top];
    };

    const spawn = (time: number, amp: number) => {
      ripples.push({ born: time, amp });
      lastSpawn = time;
    };

    const tick = () => {
      const time = performance.now() / 1000;
      const dt = Math.min(0.1, time - last);
      last = time;
      if (frame++ % 30 === 0) color = getComputedStyle(canvas).color;
      const still = reduced.matches;
      const current = modeRef.current;
      const [cx, cy] = orbCentre();
      const reach = Math.hypot(Math.max(cx, width - cx), Math.max(cy, height - cy)) * 0.85;
      const life = reach / SPEED;

      if (still) {
        ripples = [];
      } else {
        // What the session is doing decides how the field moves.
        const live = current === "listening" ? inputLevel() : current === "speaking" ? outputLevel() : 0;
        level += (live - level) * Math.min(1, dt * 12);
        const pulseEvery = current === "connecting" ? 0.8 : current === "awaiting" ? 1.6 : current === "thinking" ? 2.4 : 3.2;
        if (time - lastPulse > pulseEvery) {
          lastPulse = time;
          spawn(time, current === "connecting" ? 0.3 : current === "awaiting" ? 0.6 : 0.4);
        }
        if (level > 0.08 && time - lastSpawn > 0.16) spawn(time, Math.min(1, 0.25 + level * 1.6));
        sweep = current === "thinking" ? sweep + dt * 2.2 : 0;
        ripples = ripples.filter((ripple) => time - ripple.born < life);
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = color;
      // Align the grid so one dot sits under the orb's centre.
      const x0 = cx - Math.ceil(cx / PITCH) * PITCH;
      const y0 = cy - Math.ceil(cy / PITCH) * PITCH;
      for (let y = y0; y < height + PITCH; y += PITCH) {
        for (let x = x0; x < width + PITCH; x += PITCH) {
          const dx = x - cx;
          const dy = y - cy;
          const d = Math.hypot(dx, dy);
          if (d < ORB_CLEARANCE) continue;
          const falloff = Math.max(0, 1 - d / reach) ** 1.5;
          if (falloff <= 0) continue;
          let energy = 0;
          for (const ripple of ripples) {
            const age = time - ripple.born;
            const offset = (d - age * SPEED) / RING;
            if (offset > -3 && offset < 3) energy += ripple.amp * Math.exp(-offset * offset) * (1 - age / life);
          }
          if (current === "thinking" && !still) {
            const diff = Math.atan2(dy, dx) - sweep;
            energy += 0.55 * Math.max(0, Math.cos(diff)) ** 14;
          }
          energy = Math.min(1, energy);
          const push = energy * 3;
          const px = x + (dx / d) * push;
          const py = y + (dy / d) * push;
          const r = 0.7 + energy * 1.1;
          ctx.globalAlpha = (0.12 + energy * 0.7) * falloff;
          ctx.fillRect(px - r, py - r, r * 2, r * 2);
        }
      }
      ctx.globalAlpha = 1;
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(handle);
      observer.disconnect();
    };
  }, [inputLevel, outputLevel]);

  return <canvas ref={canvasRef} className="chat-voice-field" aria-hidden />;
}
