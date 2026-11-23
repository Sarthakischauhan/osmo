import type { Track } from "@osmo/osmo-composer";
import { z } from "zod";
import type { EngineFactory, MusicEngine } from "./store";

/** Local files: an audio element on the server's range-streaming route (`/api/music/local/{id}`). */
export class LocalEngine implements MusicEngine {
  private readonly audio: HTMLAudioElement;

  constructor(audio: HTMLAudioElement = new Audio()) {
    this.audio = audio;
    this.audio.preload = "auto";
  }

  async load(track: Track, gain: number): Promise<void> {
    if (!track.url) throw new Error(`${track.title} has no file to play`);
    this.audio.src = track.url;
    this.audio.volume = gain;
    await this.audio.play();
  }

  async play(): Promise<void> {
    await this.audio.play();
  }

  async pause(): Promise<void> {
    this.audio.pause();
  }

  setGain(gain: number): void {
    this.audio.volume = Math.min(1, Math.max(0, gain));
  }

  position(): number {
    return this.audio.currentTime * 1000;
  }

  listen({ ended, failed }: { ended: () => void; failed: (message: string) => void }): void {
    this.audio.addEventListener("ended", ended);
    this.audio.addEventListener("error", () => failed("This file could not be played"));
  }
}

/**
 * Spotify Connect controls a user's existing Spotify client.
 * No Widevine/CDM, Spotify playback SDK, or access tokens in the web renderer.
 */
async function spotifyControl(action: "play" | "pause" | "resume" | "next" | "previous" | "volume", uri?: string, volume?: number): Promise<void> {
  const response = await fetch("/api/music/spotify/playback", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, ...(uri ? { uri } : {}), ...(volume === undefined ? {} : { volume }) }),
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined);
    const detail = z.object({ detail: z.string() }).safeParse(body);
    throw new Error(detail.success ? detail.data.detail : `Spotify control failed (HTTP ${response.status})`);
  }
}

export function spotifyProblem(kind: "initialization_error" | "authentication_error" | "account_error" | "playback_error", message: string): string {
  if (kind === "initialization_error") return "Open the Spotify app on your device, then retry";
  if (kind === "authentication_error") return "Spotify sign-in expired: connect Spotify again";
  if (kind === "account_error") return "Spotify playback needs a Premium account";
  return `Spotify could not play this: ${message}`;
}

export class SpotifyEngine implements MusicEngine {
  private handlers: { ended: () => void; failed: (message: string) => void } | undefined;

  listen(handlers: { ended: () => void; failed: (message: string) => void }): void {
    this.handlers = handlers;
  }

  async load(track: Track, _gain: number): Promise<void> {
    if (!track.uri) throw new Error(`${track.title} has no Spotify URI`);
    await spotifyControl("play", track.uri);
  }

  async play(): Promise<void> { await spotifyControl("resume"); }
  async pause(): Promise<void> { await spotifyControl("pause"); }

  // External Spotify audio cannot be ducked from the Osmo renderer. Do not spam
  // Connect with 16 ms volume requests whenever the assistant speaks.
  setGain(_gain: number): void {}

  setVolume(volume: number): void {
    void spotifyControl("volume", undefined, volume).catch((cause: unknown) => this.handlers?.failed(String(cause)));
  }

  position(): number { return 0; }
}

export const browserEngines: EngineFactory = (source) => (source === "local" ? new LocalEngine() : new SpotifyEngine());
