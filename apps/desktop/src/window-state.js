// Where the floating orb sits and where its panel opens. Pure functions over Electron's display rectangles
// ({ x, y, width, height }), so placement is testable without a screen; the window itself is main's.

/** The floating window around the orb alone, and with the compact panel open (#18). */
export const ORB_SIZE = { width: 112, height: 112 };
export const PANEL_SIZE = { width: 360, height: 400 };


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

/** The panel opens toward the middle of the screen: up and left from an orb in the lower right, and so on. */
export function panelPlacement(orb, workArea) {
  const centerX = orb.x + orb.width / 2;
  const centerY = orb.y + orb.height / 2;
  const vertical = centerY > workArea.y + workArea.height / 2 ? 'up' : 'down';
  const horizontal = centerX > workArea.x + workArea.width / 2 ? 'left' : 'right';
  return `${vertical}-${horizontal}`;
}

/** The window with the panel open: the orb's corner stays where it is, and the window stays inside the work area. */
export function panelBounds(orb, workArea) {
  const placement = panelPlacement(orb, workArea);
  const { width, height } = PANEL_SIZE;
  const x = placement.endsWith('left') ? orb.x + orb.width - width : orb.x;
  const y = placement.startsWith('up') ? orb.y + orb.height - height : orb.y;
  return { placement, bounds: clampInto({ x, y, width, height }, workArea) };
}

/** The orb after the panel closes again: back to its own 112 px corner, wherever the panel window ended up. */
export function orbBoundsIn(panel, placement) {
  return {
    ...ORB_SIZE,
    x: placement.endsWith('left') ? panel.x + panel.width - ORB_SIZE.width : panel.x,
    y: placement.startsWith('up') ? panel.y + panel.height - ORB_SIZE.height : panel.y,
  };
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

