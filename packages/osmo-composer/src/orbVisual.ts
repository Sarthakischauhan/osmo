import type { OsmoState } from "./protocol";

/**
 * Canvas orb: a fluid, layered blob whose wobble, size and glow follow the assistant's output level.
 * The idea (state presets plus a live 0..1 volume that adds speed, distortion and glow) follows
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

type Rgb = readonly [number, number, number];

interface Preset {
  /** Light, mid and deep colour of the body. */
  colors: readonly [Rgb, Rgb, Rgb];
  /** Base wobble (0..1), animation speed, and breathing amount. */
  wobble: number;
  speed: number;
  breathe: number;
  glow: number;
}

export const PRESETS: Record<OrbMode, Preset> = {
  // Calm brand indigo, nearly still.
  idle: { colors: [[226, 230, 255], [140, 150, 240], [62, 58, 170]], wobble: 0.12, speed: 0.35, breathe: 0.01, glow: 0.25 },
  // Same brand family, a little more alive while the mic is open.
  listening: { colors: [[230, 236, 255], [129, 146, 248], [67, 56, 202]], wobble: 0.28, speed: 0.6, breathe: 0.03, glow: 0.4 },
  // Muted slate with a quick breath, so it never reads as ready.
  connecting: { colors: [[214, 218, 228], [140, 148, 168], [70, 76, 96]], wobble: 0.1, speed: 1.6, breathe: 0.05, glow: 0.2 },
  // Amber only while a widget waits on the user's choice.
  awaiting: { colors: [[255, 236, 190], [251, 191, 36], [180, 83, 9]], wobble: 0.22, speed: 0.5, breathe: 0.05, glow: 0.55 },
  // Lighter, icy lavender that swirls rather than wobbles.
  thinking: { colors: [[255, 255, 255], [221, 214, 254], [167, 139, 250]], wobble: 0.18, speed: 1.8, breathe: 0.02, glow: 0.5 },
  // Deep violet with a cyan edge; the live level drives most of the motion.
  speaking: { colors: [[224, 231, 255], [129, 140, 248], [76, 29, 149]], wobble: 0.45, speed: 1.1, breathe: 0.0, glow: 0.6 },
};

const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const mixRgb = (a: Rgb, b: Rgb, t: number): Rgb => [mix(a[0], b[0], t), mix(a[1], b[1], t), mix(a[2], b[2], t)];
const css = (c: Rgb, alpha = 1) => `rgba(${c[0].toFixed(0)}, ${c[1].toFixed(0)}, ${c[2].toFixed(0)}, ${alpha})`;

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
    colors: [mixRgb(p.colors[0], target.colors[0], k), mixRgb(p.colors[1], target.colors[1], k), mixRgb(p.colors[2], target.colors[2], k)],
    wobble: mix(p.wobble, target.wobble, k),
    speed: mix(p.speed, target.speed, k),
    breathe: mix(p.breathe, target.breathe, k),
    glow: mix(p.glow, target.glow, k),
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

/** Draw one frame. `size` is the canvas size in device pixels; the body fills about 45% of it. */
export function drawOrb(ctx: CanvasRenderingContext2D, size: number, frame: OrbFrameState, mode: OrbMode): void {
  const { preset, level, time: t } = frame;
  const [light, mid, deep] = preset.colors;
  const c = size / 2;
  const base = size * 0.24;
  const radius = base * (1 + Math.sin(t * 2) * preset.breathe + level * 0.16);
  const amount = (preset.wobble + level * 0.9) * 0.09;
  ctx.clearRect(0, 0, size, size);

  // Soft outer glow, brighter with the voice.
  const glow = ctx.createRadialGradient(c, c, radius * 0.6, c, c, Math.min(c * 0.98, radius * (1.9 + level * 0.6)));
  glow.addColorStop(0, css(mid, preset.glow * (0.55 + level * 0.45)));
  glow.addColorStop(1, css(mid, 0));
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, size, size);

  // Two translucent trailing layers make the body read as fluid.
  for (const [seed, scale, alpha] of [[4.2, 1.08, 0.35], [8.9, 1.03, 0.5]] as const) {
    blob(ctx, c, c, radius * scale, t * 0.8, amount * 1.4, seed);
    ctx.fillStyle = css(deep, alpha);
    ctx.fill();
  }

  // Main body with a lit, off-centre highlight.
  blob(ctx, c, c, radius, t, amount, 0);
  const swirl = mode === "thinking" ? t * 1.4 : t * 0.25;
  const hx = c + Math.cos(swirl - 2.3) * radius * 0.32;
  const hy = c + Math.sin(swirl - 2.3) * radius * 0.32;
  const body = ctx.createRadialGradient(hx, hy, radius * 0.05, c, c, radius * 1.15);
  body.addColorStop(0, "rgba(255, 255, 255, 1)");
  body.addColorStop(0.2, css(light));
  body.addColorStop(0.6, css(mid));
  body.addColorStop(1, css(deep));
  ctx.fillStyle = body;
  ctx.fill();
}
