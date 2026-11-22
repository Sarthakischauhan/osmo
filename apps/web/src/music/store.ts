import type { MusicCommand, MusicState, Track } from "@osmo/osmo-composer";

/**
 * The music player's state, outside React: the sidebar mini player reads it today, and a chat widget can attach to
 * the same `MusicPlayer` later. It plays a queue through one engine per source (engines.ts: an audio element for
 * local files, Spotify's Web Playback SDK for Spotify), takes the server's `music.command`s, and ducks while Osmo
 * speaks.
 */

/** How loud the music stays while Osmo speaks (GUI's ducking mockup: 70% → 16%, about a quarter). */
export const DUCK_GAIN = 0.25;
/** Ramps: down fast so the first word is clear, back up slowly so it does not jump. */
export const DUCK_DOWN_MS = 150;
export const DUCK_UP_MS = 400;
export const RAMP_STEP_MS = 16;
export const DEFAULT_VOLUME = 70;

/** One way of playing a track: it plays what it loads, at `gain` (0..1, volume and ducking already applied). */
export interface MusicEngine {
  load(track: Track, gain: number): Promise<void>;
  play(): Promise<void>;
  pause(): Promise<void>;
  setGain(gain: number): void;
  /** Where playback is, ms. */
  position(): number;
  /** Set once by the player: the track ended (go on), or it could not play (say why). */
  listen(handlers: { ended: () => void; failed: (message: string) => void }): void;
}

export type EngineFactory = (source: Track["source"]) => MusicEngine;

export interface MusicSnapshot {
  queue: readonly Track[];
  index: number;
  track: Track | undefined;
  playing: boolean;
  /** 0..100, what the user (or Osmo) set. */
  volume: number;
  ducked: boolean;
  /** What the engine plays at now, 0..1 (volume × ducking, mid-ramp while it moves). */
  gain: number;
  error: string | undefined;
}

interface Timers {
  setTimeout: (callback: () => void, ms: number) => number;
  clearTimeout: (id: number) => void;
}

const browserTimers: Timers = {
  setTimeout: (callback, ms) => window.setTimeout(callback, ms),
  clearTimeout: (id) => window.clearTimeout(id),
};

export class MusicPlayer {
  private snapshot: MusicSnapshot = { queue: [], index: 0, track: undefined, playing: false, volume: DEFAULT_VOLUME, ducked: false, gain: DEFAULT_VOLUME / 100, error: undefined };
  private readonly listeners = new Set<() => void>();
  private readonly engines = new Map<Track["source"], MusicEngine>();
  private ramp: number | undefined;

  constructor(private readonly makeEngine: EngineFactory, private readonly timers: Timers = browserTimers) {}

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly getSnapshot = (): MusicSnapshot => this.snapshot;

  /** What to tell the server (`music.state`). */
  state(): MusicState {
    const { track, playing, volume, error } = this.snapshot;
    const engine = track ? this.engines.get(track.source) : undefined;
    return { type: "music.state", playing, track: track ?? null, positionMs: Math.max(0, Math.round(engine?.position() ?? 0)), volume, error: error ?? null };
  }

  async command(command: MusicCommand): Promise<void> {
    switch (command.action) {
      case "play":
        if (command.tracks.length > 0) await this.playQueue(command.tracks, 0);
        return;
      case "pause":
        return this.pause();
      case "resume":
        return this.resume();
      case "next":
        return this.skip(1);
      case "previous":
        return this.skip(-1);
      case "volume":
        if (command.volume != null) this.setVolume(command.volume);
        return;
    }
  }

  async playQueue(queue: readonly Track[], index: number): Promise<void> {
    const track = queue[index];
    if (!track) return;
    const previous = this.snapshot.track;
    if (previous && previous.source !== track.source) await this.engine(previous.source).pause();
    this.set({ queue, index, track, playing: true, error: undefined });
    try {
      await this.engine(track.source).load(track, this.snapshot.gain);
    } catch (cause) {
      this.fail(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async toggle(): Promise<void> {
    return this.snapshot.playing ? this.pause() : this.resume();
  }

  async pause(): Promise<void> {
    const { track } = this.snapshot;
    if (!track) return;
    this.set({ playing: false });
    await this.engine(track.source).pause();
  }

  async resume(): Promise<void> {
    const { track } = this.snapshot;
    if (!track) return;
    this.set({ playing: true, error: undefined });
    try {
      await this.engine(track.source).play();
    } catch (cause) {
      this.fail(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async skip(step: 1 | -1): Promise<void> {
    const { queue, index } = this.snapshot;
    const next = index + step;
    if (next < 0 || next >= queue.length) {
      if (step === 1) await this.pause();
      return;
    }
    await this.playQueue(queue, next);
  }

  setVolume(volume: number): void {
    const clamped = Math.round(Math.min(100, Math.max(0, volume)));
    this.set({ volume: clamped });
    this.rampTo(this.target(), DUCK_UP_MS);
  }

  /** Osmo started or stopped speaking: dip the music under its voice, then bring it back. */
  duck(on: boolean): void {
    if (on === this.snapshot.ducked) return;
    this.set({ ducked: on });
    this.rampTo(this.target(), on ? DUCK_DOWN_MS : DUCK_UP_MS);
  }

  private target(): number {
    return (this.snapshot.volume / 100) * (this.snapshot.ducked ? DUCK_GAIN : 1);
  }

  /** Move the gain to `to` in even steps over `ms`; a newer ramp replaces one still going. */
  private rampTo(to: number, ms: number): void {
    if (this.ramp !== undefined) this.timers.clearTimeout(this.ramp);
    const from = this.snapshot.gain;
    const steps = Math.max(1, Math.round(ms / RAMP_STEP_MS));
    const step = (n: number) => {
      const gain = n >= steps ? to : from + ((to - from) * n) / steps;
      this.setGain(gain);
      this.ramp = n >= steps ? undefined : this.timers.setTimeout(() => step(n + 1), RAMP_STEP_MS);
    };
    step(1);
  }

  private setGain(gain: number): void {
    this.set({ gain });
    const { track } = this.snapshot;
    if (track) this.engine(track.source).setGain(gain);
  }

  private engine(source: Track["source"]): MusicEngine {
    let engine = this.engines.get(source);
    if (!engine) {
      engine = this.makeEngine(source);
      engine.listen({
        ended: () => {
          if (this.snapshot.track?.source === source) void this.skip(1);
        },
        failed: (message) => {
          if (this.snapshot.track?.source === source) this.fail(message);
        },
      });
      this.engines.set(source, engine);
    }
    return engine;
  }

  private fail(message: string): void {
    this.set({ playing: false, error: message });
  }

  private set(change: Partial<MusicSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...change };
    for (const listener of this.listeners) listener();
  }
}
