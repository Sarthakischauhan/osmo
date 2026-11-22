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

/** The bits of Spotify's Web Playback SDK Osmo uses (https://developer.spotify.com/documentation/web-playback-sdk). */
interface SpotifyPlayerState {
  paused: boolean;
  position: number;
  track_window: { current_track: { uri: string } | null; previous_tracks: { uri: string }[] };
}
interface SpotifyPlayer {
  connect(): Promise<boolean>;
  pause(): Promise<void>;
  resume(): Promise<void>;
  setVolume(volume: number): Promise<void>;
  addListener(event: "ready", callback: (data: { device_id: string }) => void): void;
  addListener(event: "player_state_changed", callback: (state: SpotifyPlayerState | null) => void): void;
  addListener(event: "initialization_error" | "authentication_error" | "account_error" | "playback_error", callback: (error: { message: string }) => void): void;
}
interface SpotifySdk {
  Player: new (options: { name: string; getOAuthToken: (give: (token: string) => void) => void; volume: number }) => SpotifyPlayer;
}
declare global {
  interface Window {
    Spotify?: SpotifySdk;
    onSpotifyWebPlaybackSDKReady?: () => void;
  }
}

export const SPOTIFY_SDK_URL = "https://sdk.scdn.co/spotify-player.js";
const tokenSchema = z.object({ accessToken: z.string(), expiresAt: z.number() });

/** Why Spotify could not start, in words the player can show. */
export function spotifyProblem(kind: "initialization_error" | "authentication_error" | "account_error" | "playback_error", message: string): string {
  if (kind === "initialization_error") return "Spotify needs Widevine: play it in the Osmo desktop app or Chrome";
  if (kind === "authentication_error") return "Spotify sign-in expired: connect Spotify again";
  if (kind === "account_error") return "Spotify playback needs a Premium account";
  return `Spotify could not play this: ${message}`;
}

async function spotifyToken(): Promise<string> {
  const response = await fetch("/api/music/spotify/token");
  if (!response.ok) throw new Error("Spotify is not connected");
  return tokenSchema.parse(await response.json()).accessToken;
}

function loadSdk(): Promise<SpotifySdk> {
  return new Promise((resolve, reject) => {
    if (window.Spotify) return resolve(window.Spotify);
    window.onSpotifyWebPlaybackSDKReady = () => (window.Spotify ? resolve(window.Spotify) : reject(new Error("Spotify's player did not load")));
    const script = document.createElement("script");
    script.src = SPOTIFY_SDK_URL;
    script.async = true;
    script.onerror = () => reject(new Error("Spotify's player could not be loaded"));
    document.head.append(script);
  });
}

/**
 * Spotify through its Web Playback SDK: Osmo's window becomes a Spotify Connect device, and a play goes to it over
 * the Web API. It needs Widevine (castLabs ECS in the desktop app, or Chrome) and a Premium account.
 */
export class SpotifyEngine implements MusicEngine {
  private player: Promise<{ player: SpotifyPlayer; device: string }> | undefined;
  private handlers: { ended: () => void; failed: (message: string) => void } | undefined;
  private current: string | undefined;
  private at = 0;

  listen(handlers: { ended: () => void; failed: (message: string) => void }): void {
    this.handlers = handlers;
  }

  private connect(gain: number): Promise<{ player: SpotifyPlayer; device: string }> {
    this.player ??= loadSdk().then(
      (sdk) =>
        new Promise((resolve, reject) => {
          const player = new sdk.Player({ name: "Osmo", volume: gain, getOAuthToken: (give) => void spotifyToken().then(give, (cause) => this.handlers?.failed(String(cause))) });
          player.addListener("ready", ({ device_id }) => resolve({ player, device: device_id }));
          for (const kind of ["initialization_error", "authentication_error", "account_error", "playback_error"] as const) {
            player.addListener(kind, ({ message }) => {
              const problem = spotifyProblem(kind, message);
              this.handlers?.failed(problem);
              reject(new Error(problem));
            });
          }
          player.addListener("player_state_changed", (state) => {
            if (!state) return;
            this.at = state.position;
            // Spotify reports the end as a paused state at 0 with the track moved into the history.
            if (state.paused && state.position === 0 && state.track_window.previous_tracks.some((t) => t.uri === this.current)) this.handlers?.ended();
          });
          void player.connect();
        }),
    );
    return this.player;
  }

  async load(track: Track, gain: number): Promise<void> {
    if (!track.uri) throw new Error(`${track.title} has no Spotify uri`);
    const { device } = await this.connect(gain);
    this.current = track.uri;
    const response = await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${encodeURIComponent(device)}`, {
      method: "PUT",
      headers: { authorization: `Bearer ${await spotifyToken()}`, "content-type": "application/json" },
      body: JSON.stringify({ uris: [track.uri] }),
    });
    if (!response.ok) throw new Error(response.status === 403 ? spotifyProblem("account_error", "") : `Spotify refused to play (HTTP ${response.status})`);
  }

  async play(): Promise<void> {
    await (await this.player)?.player.resume();
  }

  async pause(): Promise<void> {
    await (await this.player)?.player.pause();
  }

  setGain(gain: number): void {
    void this.player?.then(({ player }) => player.setVolume(Math.min(1, Math.max(0.0001, gain))));
  }

  position(): number {
    return this.at;
  }
}

export const browserEngines: EngineFactory = (source) => (source === "local" ? new LocalEngine() : new SpotifyEngine());
