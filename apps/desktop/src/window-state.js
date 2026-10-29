// Where the floating orb sits. Pure functions over Electron's display rectangles
// ({ x, y, width, height }), so placement is testable without a screen; the window itself is main's.

/** The floating window around the orb alone (#18). */
export const ORB_SIZE = { width: 112, height: 112 };


function overlap(a, b) {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? width * height : 0;
}

/** `rect` moved, never resized, to lie inside `workArea`. */
export function clampInto(rect, workArea) {
  return {
    ...rect,
    x: Math.min(Math.max(rect.x, workArea.x), workArea.x + workArea.width - rect.width),
    y: Math.min(Math.max(rect.y, workArea.y), workArea.y + workArea.height - rect.height),
  };
}

/** The work area holding most of `rect`, or undefined when it is on none (its screen was unplugged). */
export function workAreaOf(rect, workAreas) {
  let best;
  let most = 0;
  for (const candidate of workAreas) {
    const shared = overlap(rect, candidate);
    if (shared > most) { best = candidate; most = shared; }
  }
  return best;
}

/** Flush-corner default: the lower right of `workArea`, `margin` px in from both edges. */
export function defaultOrbBounds(workArea, margin) {
  return {
    ...ORB_SIZE,
    x: workArea.x + workArea.width - ORB_SIZE.width - margin,
    y: workArea.y + workArea.height - ORB_SIZE.height - margin,
  };
}

/**
 * The orb's bounds at start: where the user left it if some screen still shows it (kept inside that screen, so a
 * smaller resolution cannot strand it), else the default on the primary display.
 */
export function restoreOrbBounds(saved, workAreas, primary, margin) {
  if (saved) {
    const rect = { ...ORB_SIZE, x: saved.x, y: saved.y };
    const home = workAreaOf(rect, workAreas);
    if (home) return clampInto(rect, home);
  }
  return defaultOrbBounds(primary, margin);
}

/** What is stored: the orb's position, or undefined for anything else (a missing, old or damaged file). */
export function parseSavedPosition(text) {
  try {
    const value = JSON.parse(text);
    return Number.isFinite(value?.x) && Number.isFinite(value?.y) ? { x: value.x, y: value.y } : undefined;
  } catch { return undefined; }
}

export function serializePosition(orb) {
  return JSON.stringify({ x: Math.round(orb.x), y: Math.round(orb.y) });
}

