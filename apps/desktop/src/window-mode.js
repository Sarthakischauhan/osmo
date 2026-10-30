// The switch between the two windows: the full workspace, or the always-on-top orb. One is shown at a time, and
// switching only shows and hides them, so the session (main's) and both renderers carry on untouched.

export const SWITCH_ACCELERATOR = 'CommandOrControl+Shift+O';

/**
 * Starts with neither showing: the app calls `toOrb()` once its windows exist.
 * @typedef {'full' | 'orb'} Mode
 * @param {{
 *   full: { isMinimized(): boolean, restore(): void, show(): void, focus(): void, hide(): void },
 *   orb: { show(): void, hide(): void },
 * }} windows
 */
export function createWindowSwitch({ full, orb }) {
  /** @type {Mode} */
  let mode = 'full';
  const toOrb = () => {
    mode = 'orb';
    full.hide();
    orb.show();
  };
  const toFull = () => {
    mode = 'full';
    orb.hide();
    // A window hidden while minimized comes back minimized unless it is restored first.
    if (full.isMinimized()) full.restore();
    full.show();
    full.focus();
  };
  return {
    get mode() { return mode; },
    toOrb,
    toFull,
    /** The global shortcut: whichever window is not showing. */
    toggle() { mode === 'full' ? toOrb() : toFull(); },
  };
}
