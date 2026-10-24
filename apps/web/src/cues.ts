import { isActionableWidget } from "@osmo/osmo-widgets";
import type { OsmoConnection, ServerEvent } from "@osmo/osmo-composer";
import { type SoundName, play, unlock } from "cuelume";


/** Interaction sounds via Cuelume (https://cuelume.dev, MIT), mapped per its docs. */
export const CUES = {
  optionPick: "select",
  confirm: "success",
  widgetAppear: "ready",
  sessionStart: "open",
  sessionEnd: "close",
  awaitingChoice: "attention",
} as const satisfies Record<string, SoundName>;

export type CueEvent = keyof typeof CUES;

/** Quiet enough to sit under a voice; the passive ones quieter still. */
const VOLUME: Record<CueEvent, number> = {
  optionPick: 0.3,
  confirm: 0.35,
  widgetAppear: 0.2,
  sessionStart: 0.35,
  sessionEnd: 0.35,
  awaitingChoice: 0.3,
};

/** Cues the user didn't trigger themselves stay silent while the assistant is talking. */
const SKIP_WHILE_SPEAKING: ReadonlySet<CueEvent> = new Set(["widgetAppear", "awaitingChoice"]);

/**
 * Decides which cue an app event gets, and when it may sound: nothing before the first user gesture
 * (browser autoplay rules), passive cues never over speech, and one attention bell per pending choice.
 */
export class Cues {
  private unlocked = false;
  private connection: OsmoConnection = "disconnected";
  private lastAwaited: string | undefined;

  constructor(private readonly isSpeaking: () => boolean) {}

  /** Call from a real user gesture (pointerdown/keydown). */
  async gesture(): Promise<void> {
    if (!this.unlocked) this.unlocked = await unlock();
  }

  fire(event: CueEvent): void {
    if (!this.unlocked) return;
    if (SKIP_WHILE_SPEAKING.has(event) && this.isSpeaking()) return;
    play(CUES[event], { volume: VOLUME[event] });
  }

  onServerEvent(event: ServerEvent): void {
    // Actionable widgets get the attention bell when the orb turns amber instead.
    if (event.type === "widget.render" && !isActionableWidget(event.widget.type, event.widget.props)) {
      this.fire("widgetAppear");
    }
  }

  onConnection(next: OsmoConnection): void {
    const previous = this.connection;
    this.connection = next;
    if (next === previous) return;
    if (next === "connected") this.fire("sessionStart");
    else if (next === "disconnected" && previous === "connected") this.fire("sessionEnd");
  }

  /** The id of the choice the amber orb is waiting on, or undefined when it isn't amber. */
  onAmber(widgetId: string | undefined): void {
    if (widgetId === undefined || widgetId === this.lastAwaited) return;
    this.lastAwaited = widgetId;
    this.fire("awaitingChoice");
  }

  onOptionPick(): void {
    this.fire("optionPick");
  }

  onConfirm(): void {
    this.fire("confirm");
  }
}
