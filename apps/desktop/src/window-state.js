// Where the floating orb sits and where its hover box opens. Pure functions over Electron's display rectangles
// ({ x, y, width, height }), so placement is testable without a screen; the windows themselves are main's.

/** The floating window around the orb alone (#18), and how far in from the work area's lower right it starts. */
export const ORB_SIZE = { width: 112, height: 112 };
export const ORB_MARGIN = 24;
/** The hover box: as wide as the window while it is open, and as tall as its agent log, up to this. */
export const BOX_WIDTH = 360;
export const BOX_MAX_HEIGHT = 288;

/** The orb's own 112 px box in the lower right of `workArea`. */
export function defaultOrbBounds(workArea) {
  return {
    ...ORB_SIZE,
    x: workArea.x + workArea.width - ORB_SIZE.width - ORB_MARGIN,
    y: workArea.y + workArea.height - ORB_SIZE.height - ORB_MARGIN,
  };
}

/**
 * The floating window while the hover box is `height` tall (0: closed, the orb's own box). The box opens below the orb
 * and flips above it when it would not fit in the work area. The orb never moves on screen, so the window grows
 * around it: `orbLeft` is where the orb sits inside the window (the window is kept inside the work area sideways).
 * @returns {{ side: 'below' | 'above', orbLeft: number, bounds: { x: number, y: number, width: number, height: number } }}
 */
export function boxLayout(orb, workArea, height) {
  if (height <= 0) return { side: 'below', orbLeft: 0, bounds: { ...orb } };
  const side = orb.y + orb.height + height <= workArea.y + workArea.height ? 'below' : 'above';
  const centered = orb.x + orb.width / 2 - BOX_WIDTH / 2;
  const x = Math.min(Math.max(centered, workArea.x), workArea.x + workArea.width - BOX_WIDTH);
  return {
    side,
    orbLeft: orb.x - x,
    bounds: { x, y: side === 'below' ? orb.y : orb.y - height, width: BOX_WIDTH, height: orb.height + height },
  };
}
