import type { ClientEvent, Effort, Thread, VoiceModel, VoiceSelection } from "@osmo/osmo-composer";

/** What the Electron app's main process shows of the voice session it owns (apps/desktop/src/session-contract.js). */
export interface SessionSnapshot {
  connection: "disconnected" | "connecting" | "connected";
  state: "idle" | "listening" | "thinking" | "speaking" | "working";
  detail: string | undefined;
  error: string | undefined;
  startedAt: number | undefined;
  thread: Thread | undefined;
  selection: VoiceSelection | undefined;
  models: VoiceModel[];
}

export interface StartOptions {
  voice?: string;
  model?: string;
  effort?: Effort;
  threadId?: string;
}

/** The session half of `window.osmoDesktop`: the same calls from the workspace and the orb window. */
export interface SessionBridge {
  snapshot(): Promise<SessionSnapshot>;
  start(options: StartOptions): Promise<SessionSnapshot>;
  end(): Promise<SessionSnapshot>;
  interrupt(): Promise<void>;
  send(event: ClientEvent): Promise<void>;
  /** A server event, as main received it; validate before use. */
  onEvent(callback: (event: unknown) => void): () => void;
  onStatus(callback: (snapshot: SessionSnapshot) => void): () => void;
}

/** Where the orb window's hover box opened: the window's height is the orb's plus `height` of box. */
export interface BoxPlacement {
  side: "below" | "above";
  /** Where the orb sits from the window's left edge, px. */
  orbLeft: number;
}

export interface OrbBridge {
  /** Double-click: show the workspace and hide the orb. */
  showFull(): Promise<void>;
  /** The hover box now needs `height` px (0: closed). */
  setBox(height: number): Promise<BoxPlacement>;
}

export interface DesktopStatus { enabled: boolean; platform: string; stopShortcut: string }

export interface DesktopBridge {
  status(): Promise<DesktopStatus>;
  setEnabled(enabled: boolean): Promise<DesktopStatus>;
  onStatus(callback: (status: DesktopStatus) => void): () => void;
  session: SessionBridge;
  orb: OrbBridge;
}

declare global { interface Window { osmoDesktop?: DesktopBridge } }
