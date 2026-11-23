import type { Track } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { initials } from "../src/components/MusicMini";
import { spotifyProblem } from "../src/music/engines";
import { reportable } from "../src/music/player";
import { DUCK_DOWN_MS, DUCK_GAIN, DUCK_UP_MS, MusicPlayer, RAMP_STEP_MS, type MusicEngine } from "../src/music/store";

const track = (id: string, source: Track["source"] = "local"): Track => ({ id, source, title: `Song ${id}`, artist: "The xx", url: source === "local" ? `/api/music/local/${id}` : null, uri: source === "spotify" ? `spotify:track:${id}` : null });

class FakeEngine implements MusicEngine {
  loaded: string[] = [];
  gains: number[] = [];
  playing = false;
  fail: string | undefined;
  handlers: { ended: () => void; failed: (message: string) => void } | undefined;
  async load(t: Track, gain: number) {
    if (this.fail) throw new Error(this.fail);
    this.loaded.push(t.id);
    this.gains.push(gain);
    this.playing = true;
  }
  async play() { this.playing = true; }
  async pause() { this.playing = false; }
  setGain(gain: number) { this.gains.push(gain); }
  position() { return 1234.4; }
  listen(handlers: { ended: () => void; failed: (message: string) => void }) { this.handlers = handlers; }
}

/** Timers a test runs by hand: each ramp step is one call of `flush`. */
class ManualTimers {
  queue: (() => void)[] = [];
  setTimeout = (callback: () => void) => this.queue.push(callback);
  clearTimeout = (id: number) => { this.queue[id - 1] = () => undefined; };
  flush() { while (this.queue.length) this.queue.shift()?.(); }
}

function player() {
  const engines = { local: new FakeEngine(), spotify: new FakeEngine() };
  const timers = new ManualTimers();
  return { engines, timers, music: new MusicPlayer((source) => engines[source], timers) };
}

describe("the music player", () => {
  it("plays a command's first track, queues the rest, and goes on when one ends", async () => {
    const { engines, music } = player();
    await music.command({ type: "music.command", action: "play", tracks: [track("a"), track("b")] });
    expect(engines.local.loaded).toEqual(["a"]);
    expect(music.getSnapshot()).toMatchObject({ playing: true, index: 0, track: { id: "a" } });
    engines.local.handlers?.ended();
    await Promise.resolve();
    expect(engines.local.loaded).toEqual(["a", "b"]);
    await music.command({ type: "music.command", action: "previous", tracks: [] });
    expect(music.getSnapshot().track?.id).toBe("a");
    // Past the end of the queue it stops instead of wrapping around.
    await music.skip(1);
    await music.skip(1);
    expect(music.getSnapshot()).toMatchObject({ playing: false, track: { id: "b" } });
  });

  it("switches engines between a local file and Spotify, pausing the one it leaves", async () => {
    const { engines, music } = player();
    await music.playQueue([track("a"), track("s", "spotify")], 0);
    await music.skip(1);
    expect(engines.local.playing).toBe(false);
    expect(engines.spotify.loaded).toEqual(["s"]);
  });

  it("ducks to a quarter while Osmo speaks, ramping down fast and back up slowly", async () => {
    const { engines, timers, music } = player();
    await music.playQueue([track("a")], 0);
    music.setVolume(80);
    timers.flush();
    engines.local.gains = [];
    music.duck(true);
    expect(engines.local.gains.length).toBe(1);
    timers.flush();
    expect(engines.local.gains.length).toBe(Math.round(DUCK_DOWN_MS / RAMP_STEP_MS));
    expect(engines.local.gains.at(-1)).toBeCloseTo(0.8 * DUCK_GAIN);
    expect(music.getSnapshot()).toMatchObject({ ducked: true, volume: 80 });
    engines.local.gains = [];
    music.duck(false);
    timers.flush();
    expect(engines.local.gains.length).toBe(Math.round(DUCK_UP_MS / RAMP_STEP_MS));
    expect(engines.local.gains.at(-1)).toBeCloseTo(0.8);
    // The ramp only moves one way at a time.
    expect([...engines.local.gains].sort((x, y) => x - y)).toEqual(engines.local.gains);
  });

  it("keeps the volume Osmo set while ducked, and restores to it", async () => {
    const { timers, music } = player();
    await music.playQueue([track("a")], 0);
    music.duck(true);
    await music.command({ type: "music.command", action: "volume", tracks: [], volume: 40 });
    timers.flush();
    expect(music.getSnapshot().gain).toBeCloseTo(0.4 * DUCK_GAIN);
    music.duck(false);
    timers.flush();
    expect(music.getSnapshot().gain).toBeCloseTo(0.4);
  });

  it("reports what it plays and why it stopped", async () => {
    const { engines, music } = player();
    expect(music.state()).toEqual({ type: "music.state", playing: false, track: null, positionMs: 0, volume: 70, error: null });
    engines.spotify.fail = spotifyProblem("account_error", "premium");
    await music.playQueue([track("s", "spotify")], 0);
    expect(music.state()).toMatchObject({ playing: false, track: { id: "s" }, error: "Spotify playback needs a Premium account" });
    await music.playQueue([track("a")], 0);
    expect(music.state()).toMatchObject({ playing: true, positionMs: 1234, error: null });
    engines.local.handlers?.failed("This file could not be played");
    expect(music.getSnapshot()).toMatchObject({ playing: false, error: "This file could not be played" });
  });

  it("tells the server about changes, not about each ramp step", async () => {
    const { timers, music } = player();
    const before = music.getSnapshot();
    music.duck(true);
    timers.flush();
    expect(reportable(before, music.getSnapshot())).toBe(false);
    await music.playQueue([track("a")], 0);
    expect(reportable(before, music.getSnapshot())).toBe(true);
  });
});

describe("the mini player", () => {
  it("shows initials when a track has no cover", () => {
    expect(initials("The xx", "Crystal")).toBe("xx");
    expect(initials("Bonobo", "Kerala")).toBe("BO");
    expect(initials("Massive Attack", "Teardrop")).toBe("MA");
    expect(initials("", "Untitled")).toBe("UN");
  });

  it("says why Spotify cannot play in plain words", () => {
    expect(spotifyProblem("initialization_error", "no device")).toMatch(/Spotify app/);
    expect(spotifyProblem("authentication_error", "")).toMatch(/connect Spotify again/);
  });
});
