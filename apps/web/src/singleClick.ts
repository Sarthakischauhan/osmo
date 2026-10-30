import type { Scheduler } from "./hoverPanel";

/**
 * How long a click waits to learn it was the first of a double-click. Nothing reports the platform's own double-click
 * interval: the DOM does not expose it and Electron has no API for it (electron/electron#23837, closed unimplemented).
 * 500 ms is the Windows default (GetDoubleClickTime) and about macOS's; GTK's default is 400 ms. This is a chosen
 * number, not the user's setting.
 */
export const DOUBLE_CLICK_MS = 500;

/**
 * A click that must not fire when it turns out to be half of a double-click: the action runs once the interval passes
 * with no second click, and a double-click cancels it.
 */
export class SingleClick<Handle> {
  private pending: Handle | undefined;

  constructor(
    private readonly scheduler: Scheduler<Handle>,
    private readonly act: () => void,
  ) {}

  click(): void {
    this.cancel();
    this.pending = this.scheduler.after(() => {
      this.pending = undefined;
      this.act();
    }, DOUBLE_CLICK_MS);
  }

  cancel(): void {
    if (this.pending === undefined) return;
    this.scheduler.cancel(this.pending);
    this.pending = undefined;
  }
}
