/**
 * Pure dial geometry for the radial picker. Angles are degrees, counter-clockwise
 * from +x; screen y grows downward, so a point is (cx + r·cosθ, cy − r·sinθ).
 *
 * A ring shows `visible` slots. Item i sits at slot position p = i − offset,
 * and slot p is drawn at angle `high − p·step` (p = 0 is the high-angle end).
 */

export type RingKey = "providers" | "models" | "voices" | "effort";

export interface RingSpec {
  key: RingKey;
  radius: number;
  /** Arc ends in degrees; slot 0 sits at `high`. */
  low: number;
  high: number;
  visible: number;
  step: number;
}

/**
 * Shared option tokens, in px. The single source for both the CSS (`--rp-option-*` and
 * `--rp-ring-spacing`, set on `.rp` from here) and the ring radii below.
 */
export const OPTION_TOKENS = {
  /** Visual height of every option (pills and provider logo chips alike). */
  height: 36,
  /** Horizontal padding inside every option. */
  paddingX: 12,
  /** Corner radius (full pill): options stay round; the 3px rule is for everything else. */
  radius: 999,
  /** Gap between an option's label and its glyph (logo, meter, playing bars). */
  gap: 6,
  fontSize: 12,
  /** Minimum hit-area height; the option's ::before extends the visual height to this. */
  hit: 44,
  /** Radial distance from one ring to the next. */
  ringSpacing: 76,
} as const;

/** Radius of the innermost (providers) ring; every other ring is `ringSpacing` further out. */
const R1 = 84;
const R2 = R1 + OPTION_TOKENS.ringSpacing;
const R3 = R1 + 2 * OPTION_TOKENS.ringSpacing;

export const RINGS: Record<RingKey, RingSpec> = {
  // 44px-wide logo chips (12 + 20 + 12) 40° apart at r = 84 leave a ~13px gap between neighbours.
  providers: { key: "providers", radius: R1, low: 30, high: 150, visible: 4, step: 40 },
  models: { key: "models", radius: R2, low: 20, high: 160, visible: 5, step: 35 },
  voices: { key: "voices", radius: R3, low: 130, high: 170, visible: 3, step: 20 },
  effort: { key: "effort", radius: R3, low: 14, high: 46, visible: 3, step: 16 },
};

/** Radius bands for hit-testing, in px from the orb centre: halfway between rings. */
export const BANDS = {
  orb: 56,
  r1: R1 + OPTION_TOKENS.ringSpacing / 2,
  r2: R2 + OPTION_TOKENS.ringSpacing / 2,
  r3: R3 + OPTION_TOKENS.ringSpacing / 2,
} as const;

/** The tokens as CSS custom properties for `.rp`'s inline style. */
export function optionTokenVars(): Record<`--rp-${string}`, string> {
  const t = OPTION_TOKENS;
  return {
    "--rp-option-height": `${t.height}px`,
    "--rp-option-padding-x": `${t.paddingX}px`,
    "--rp-option-radius": `${t.radius}px`,
    "--rp-option-gap": `${t.gap}px`,
    "--rp-option-font-size": `${t.fontSize}px`,
    "--rp-option-hit": `${t.hit}px`,
    "--rp-ring-spacing": `${t.ringSpacing}px`,
  };
}

export const SNAP_IDLE_MS = 120;
export const SNAP_MS = 180;
export const FREEZE_AFTER_SNAP_MS = 150;
export const CLOSE_GRACE_MS = 250;
export const PREVIEW_HOVER_MS = 400;
/** A pointer rests this long on a provider or model before it replaces the open path. */
export const HOVER_INTENT_MS = 160;
/** Wheel/trackpad pixels per slot. */
export const PX_PER_SLOT = 60;

/**
 * The ring as laid out for `count` items. Providers with fewer items than slots
 * are spread evenly around 90° at the ring's spacing instead of bunching at one
 * end; every other ring keeps its spec.
 */
export function ringSpec(key: RingKey, count: number): RingSpec {
  const ring = RINGS[key];
  if (key !== "providers" || count <= 0 || count >= ring.visible) return ring;
  const half = (ring.step * (count - 1)) / 2;
  return { ...ring, visible: count, low: 90 - half, high: 90 + half };
}

export function slotAngle(ring: RingSpec, position: number): number {
  return ring.high - position * ring.step;
}

export function polar(cx: number, cy: number, radius: number, angleDeg: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy - radius * Math.sin(rad) };
}

/** Allowed offsets. With fewer items than slots the group may slide along the arc (offset ≤ 0). */
export function offsetRange(ring: RingSpec, count: number): { min: number; max: number } {
  const spare = count - ring.visible;
  return { min: Math.min(0, spare), max: Math.max(0, spare) };
}

export function clampOffset(ring: RingSpec, count: number, offset: number): number {
  const { min, max } = offsetRange(ring, count);
  return Math.min(max, Math.max(min, offset));
}

/** Nearest whole-slot offset, clamped: where a scroll settles. */
export function snapOffset(ring: RingSpec, count: number, offset: number): number {
  return clampOffset(ring, count, Math.round(offset));
}

/** Slot position nearest an angle (e.g. the hovered provider's), clamped to the window. */
export function nearestSlot(ring: RingSpec, angleDeg: number): number {
  return Math.min(ring.visible - 1, Math.max(0, Math.round((ring.high - angleDeg) / ring.step)));
}

/**
 * Offset that shows item `anchor` at slot `slot` (the centre slot by default), clamped.
 * Used on ring open: anchor is the selected item, else 0.
 */
export function offsetFor(ring: RingSpec, count: number, anchor: number, slot = Math.floor(ring.visible / 2)): number {
  return clampOffset(ring, count, anchor - slot);
}

/**
 * Edge falloff: 1 inside the window, easing to 0 over the last half-slot past
 * either end (p < 0 or p > visible − 1); 0 beyond.
 */
export function edgeFalloff(ring: RingSpec, position: number): number {
  const outside = Math.max(-position, position - (ring.visible - 1), 0);
  return Math.max(0, 1 - outside / 0.5);
}

/** Opacity and scale for an item at slot position p. */
export function itemPresence(ring: RingSpec, position: number): { opacity: number; scale: number } {
  const f = edgeFalloff(ring, position);
  return { opacity: f, scale: 0.7 + 0.3 * f };
}

/** Whether "…" shows past each end: items exist beyond the window there. */
export function moreMarkers(ring: RingSpec, count: number, offset: number): { before: boolean; after: boolean } {
  return { before: offset > 0.01, after: offset + ring.visible - 1 < count - 1 - 0.01 };
}

export type Hit =
  | { ring: "orb" }
  | { ring: "providers" | "models" | "voices" | "effort"; angle: number }
  | { ring: "none" };

/** Which band a point is in, by radius; R3's side by angle (> 90° voices, < 90° effort). */
export function hitTest(cx: number, cy: number, x: number, y: number): Hit {
  const dx = x - cx;
  const dy = cy - y;
  const r = pointerRadius(dx, dy);
  if (r < BANDS.orb) return { ring: "orb" };
  const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
  if (angle < 0 || r >= BANDS.r3) return { ring: "none" };
  if (r < BANDS.r1) return { ring: "providers", angle };
  if (r < BANDS.r2) return { ring: "models", angle };
  return angle > 90 ? { ring: "voices", angle } : { ring: "effort", angle };
}

/** Pointer distance from the centre, rounded to 1/1000 px so a point on a band edge (e.g. one
 * built with `polar`, whose sin/cos lose the last bit) lands in the band it is on, not inside. */
function pointerRadius(dx: number, dy: number): number {
  return Math.round(Math.hypot(dx, dy) * 1000) / 1000;
}

/** Item index under an angle for a ring at `offset`, or undefined if between/outside items. */
export function itemAt(ring: RingSpec, count: number, offset: number, angleDeg: number): number | undefined {
  const position = (ring.high - angleDeg) / ring.step;
  const index = Math.round(position + offset);
  if (index < 0 || index >= count) return undefined;
  if (Math.abs(position + offset - index) > 0.5) return undefined;
  return edgeFalloff(ring, index - offset) < 0.5 ? undefined : index;
}

/** Ease-out cubic, for the snap glide. */
export function easeOut(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return 1 - (1 - c) ** 3;
}
