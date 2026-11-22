/** How far the pointer must travel, px, before a press on the orb is a drag rather than a tap. */
export const DRAG_THRESHOLD = 4;

/**
 * A press on the orb that may become a drag: it moves the window by each step of the pointer on screen once it has
 * travelled past the threshold, and the click that ends a drag is not a tap. Screen coordinates, because the window
 * moves under the pointer while it drags.
 */
export class OrbDrag {
  private last: { x: number; y: number } | undefined;
  private travelled = 0;
  private dragging = false;
  private dragged = false;

  constructor(private readonly move: (dx: number, dy: number) => void) {}

  down(x: number, y: number): void {
    this.last = { x, y };
    this.travelled = 0;
    this.dragging = false;
  }

  /** @returns whether this press is now a drag. */
  moveTo(x: number, y: number): boolean {
    if (!this.last) return false;
    const dx = x - this.last.x;
    const dy = y - this.last.y;
    if (!this.dragging) {
      this.travelled = Math.max(this.travelled, Math.hypot(dx, dy));
      if (this.travelled < DRAG_THRESHOLD) return false;
      this.dragging = true;
    }
    this.last = { x, y };
    if (dx !== 0 || dy !== 0) this.move(dx, dy);
    return true;
  }

  up(): void {
    if (this.dragging) this.dragged = true;
    this.last = undefined;
    this.dragging = false;
  }

  get active(): boolean { return this.dragging; }

  /** The click the browser sends after the press: false (and forgotten) when the press was a drag. */
  takeClick(): boolean {
    const tap = !this.dragged;
    this.dragged = false;
    return tap;
  }
}
