import type { OsmoState } from "./protocol";

/**
 * Canvas orb: a solid blob whose wobble and size follow the assistant's output level.
 * The idea (state presets plus a live 0..1 volume that adds speed and distortion) follows
 * assistant-ui's VoiceOrb (https://www.assistant-ui.com/elements/orb, MIT); this 2D-canvas version
 * is Osmo's own code, with no dependency and no copied source.
 */
export type OrbMode = "connecting" | "idle" | "listening" | "awaiting" | "thinking" | "speaking";

export function orbMode(connection: "disconnected" | "connecting" | "connected", state: OsmoState, awaitingChoice: boolean): OrbMode {
  if (connection === "connecting") return "connecting";
  if (connection === "disconnected") return "idle";
  if (state === "speaking") return "speaking";
  if (state === "thinking" || state === "working") return "thinking";
  return awaitingChoice ? "awaiting" : state === "idle" ? "idle" : "listening";
}

interface Preset {
  /** Opacity of the solid fill: dim while idle or connecting, full while live. */
  opacity: number;
  /** Base wobble (0..1), animation speed, and breathing amount. */
  wobble: number;
  speed: number;
  breathe: number;
}

/** States differ by opacity and motion only; the fill is always the theme's solid --foreground (osmo-design). */
export const PRESETS: Record<OrbMode, Preset> = {
  idle: { opacity: 0.4, wobble: 0.12, speed: 0.35, breathe: 0.01 },
  listening: { opacity: 1, wobble: 0.28, speed: 0.6, breathe: 0.03 },
  connecting: { opacity: 0.4, wobble: 0.1, speed: 1.6, breathe: 0.05 },
  awaiting: { opacity: 1, wobble: 0.22, speed: 0.5, breathe: 0.05 },
  thinking: { opacity: 0.7, wobble: 0.18, speed: 1.8, breathe: 0.02 },
  speaking: { opacity: 1, wobble: 0.45, speed: 1.1, breathe: 0 },
};

const mix = (a: number, b: number, t: number) => a + (b - a) * t;

/** Mutable per-canvas animation state, kept in a ref so frames never touch React state. */
export interface OrbFrameState {
  preset: Preset;
  level: number;
  time: number;
}

export function initialFrame(mode: OrbMode): OrbFrameState {
  return { preset: PRESETS[mode], level: 0, time: 0 };
}

/** Ease the shown preset and level toward their targets; returns the smoothed level. */
export function step(frame: OrbFrameState, mode: OrbMode, rawLevel: number, dt: number, still: boolean): void {
  const target = PRESETS[mode];
  const k = Math.min(1, dt * 4);
  const p = frame.preset;
  frame.preset = {
    opacity: mix(p.opacity, target.opacity, k),
    wobble: mix(p.wobble, target.wobble, k),
    speed: mix(p.speed, target.speed, k),
    breathe: mix(p.breathe, target.breathe, k),
  };
  const level = mode === "speaking" ? rawLevel : 0;
  // Fast attack, slow release, like a VU meter.
  frame.level = mix(frame.level, level, level > frame.level ? Math.min(1, dt * 18) : Math.min(1, dt * 5));
  if (!still) frame.time += dt * (frame.preset.speed + frame.level * 2.5);
}

/** Radius of the blob edge at angle `a`. */
function edge(radius: number, a: number, t: number, amount: number, seed: number): number {
  const n =
    Math.sin(2 * a + t * 1.3 + seed) * 0.5 +
    Math.sin(3 * a - t * 1.7 + seed * 2.1) * 0.3 +
    Math.sin(5 * a + t * 2.3 + seed * 3.7) * 0.2;
  return radius * (1 + n * amount);
}

function blob(ctx: CanvasRenderingContext2D, cx: number, cy: number, radius: number, t: number, amount: number, seed: number): void {
  const steps = 96;
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    const r = edge(radius, a, t, amount, seed);
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

/** Draw one frame: a solid `fill` blob. `size` is the canvas size in device pixels; the body fills about 45% of it. */
export function drawOrb(ctx: CanvasRenderingContext2D, size: number, frame: OrbFrameState, fill: string): void {
  const { preset, level, time: t } = frame;
  const c = size / 2;
  const radius = size * 0.24 * (1 + Math.sin(t * 2) * preset.breathe + level * 0.16);
  const amount = (preset.wobble + level * 0.9) * 0.09;
  ctx.clearRect(0, 0, size, size);
  blob(ctx, c, c, radius, t, amount, 0);
  ctx.globalAlpha = preset.opacity;
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.globalAlpha = 1;
}
