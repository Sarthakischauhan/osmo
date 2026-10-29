/** The hover panel's timing (#18): opens 200 ms after the pointer reaches the orb, closes 500 ms after it has left the orb and the panel. */
export const OPEN_DELAY_MS = 200;
export const CLOSE_DELAY_MS = 500;

export type Region = "orb" | "panel";

export interface Scheduler<Handle> {
  after: (callback: () => void, ms: number) => Handle;
  cancel: (handle: Handle) => void;
}

/**
 * Whether the compact panel is open. The pointer over the orb or the panel keeps it (or, from rest, opens it after a
 * delay); the close delay is what lets the pointer cross the gap between them. User interaction pins it open and a
 * waiting approval holds it; neither closes it while it lasts.
 */
export class HoverPanel<Handle> {
  open = false;
  private readonly inside = new Set<Region>();
  private pinned = false;
  private approval = false;
  private timer: Handle | undefined;

  constructor(
    private readonly scheduler: Scheduler<Handle>,
    private readonly onChange: (open: boolean) => void,
  ) {}

  enter(region: Region): void {
    this.inside.add(region);
    this.settle();
  }

  leave(region: Region): void {
    this.inside.delete(region);
    this.settle();
  }

  pin(): void {
    this.pinned = true;
    this.settle();
  }

  release(): void {
    this.pinned = false;
    this.settle();
  }

  /** An approval waits: the panel is visible until it is answered. */
  approvalWaiting(waiting: boolean): void {
    this.approval = waiting;
    this.settle();
  }

  dispose(): void {
    this.cancelTimer();
  }

  private held(): boolean {
    return this.pinned || this.approval;
  }

  private settle(): void {
    this.cancelTimer();
    if (this.held()) {
      this.set(true);
      return;
    }
    const wanted = this.inside.size > 0;
    if (wanted === this.open) return;
    this.timer = this.scheduler.after(() => {
      this.timer = undefined;
      this.set(wanted);
    }, wanted ? OPEN_DELAY_MS : CLOSE_DELAY_MS);
  }

  private set(open: boolean): void {
    if (open === this.open) return;
    this.open = open;
    this.onChange(open);
  }

  private cancelTimer(): void {
    if (this.timer !== undefined) this.scheduler.cancel(this.timer);
    this.timer = undefined;
  }
}
