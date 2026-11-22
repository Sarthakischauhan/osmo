import { describe, expect, it } from "vitest";
import { ECHO_GUARD, EchoGuard, isUserSpeech, rms } from "../src/echoGuard";
import { floatToPcm16 } from "../src/pcm";

const RATE = 24000;

/** A 100 ms frame of a 220 Hz tone at `amplitude` (its RMS is amplitude / sqrt 2), or of silence. */
function frame(amplitude: number): ArrayBuffer {
  const samples = new Float32Array(RATE / 10);
  for (let i = 0; i < samples.length; i++) samples[i] = amplitude * Math.sin((2 * Math.PI * 220 * i) / RATE);
  return floatToPcm16(samples);
}

describe("echo guard", () => {
  it("measures loudness", () => {
    expect(rms(new Float32Array([0, 0]))).toBe(0);
    expect(rms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5);
    expect(rms(new Float32Array(0))).toBe(0);
  });

  it("calls speech the user's only when loud in itself and next to what Osmo plays", () => {
    expect(isUserSpeech(0.01, 0)).toBe(false); // too quiet: room noise
    expect(isUserSpeech(0.1, 0)).toBe(true);
    expect(isUserSpeech(0.1, 0.3)).toBe(false); // Osmo is loud: this is its echo
    expect(isUserSpeech(0.2, 0.3)).toBe(true); // louder than an echo could be
    expect(isUserSpeech(0.05, 0.05, { ...ECHO_GUARD, ratio: 2 })).toBe(false);
  });

  it("passes frames straight through while Osmo is silent", () => {
    const guard = new EchoGuard(RATE);
    const quiet = frame(0.001);
    expect(guard.push(quiet, false, 0, 0)).toEqual({ send: [quiet], bargeIn: false });
  });

  it("holds back Osmo's own echo while it plays, so the server never hears itself", () => {
    const guard = new EchoGuard(RATE);
    // The echo is a quarter of the output: never the user, however long it goes on.
    for (let t = 0; t < 3000; t += 100) {
      expect(guard.push(frame(0.1), true, 0.28, t)).toEqual({ send: [], bargeIn: false });
    }
    // After playback ends, never hold the first words of a new user utterance.
    expect(guard.push(frame(0.01), false, 0, 3100).send).toHaveLength(1);
    const after = frame(0.01);
    expect(guard.push(after, false, 0, 3500).send).toEqual([after]);
  });

  it("barges in after 150 ms of the user's speech over Osmo and sends what it held as pre-roll", () => {
    const guard = new EchoGuard(RATE);
    const echo = frame(0.02);
    expect(guard.push(echo, true, 0.1, 0).bargeIn).toBe(false);
    const first = frame(0.4);
    // 100 ms of speech is two blocks: not yet (a cough or a click is not a barge-in).
    expect(guard.push(first, true, 0.1, 100)).toEqual({ send: [], bargeIn: false });
    const second = frame(0.4);
    const step = guard.push(second, true, 0.1, 200);
    expect(step.bargeIn).toBe(true);
    expect(step.send).toEqual([echo, first, second]);
    // Until the caller has stopped playback, what follows goes straight to the server.
    const next = frame(0.4);
    expect(guard.push(next, true, 0.1, 300)).toEqual({ send: [next], bargeIn: false });
    // Playback stopped, then Osmo talks again: guarded again.
    expect(guard.push(frame(0.01), false, 0, 400).send).toHaveLength(1);
    expect(guard.push(frame(0.05), true, 0.3, 500).send).toEqual([]);
  });

  it("needs the speech blocks in a row, and keeps only the last few frames", () => {
    const guard = new EchoGuard(RATE, { ...ECHO_GUARD, preroll: 2 });
    for (let t = 0; t < 1000; t += 200) {
      expect(guard.push(frame(0.4), true, 0.1, t).bargeIn).toBe(false);
      expect(guard.push(frame(0.001), true, 0.1, t + 100).bargeIn).toBe(false);
    }
    guard.push(frame(0.4), true, 0.1, 1000);
    const step = guard.push(frame(0.4), true, 0.1, 1100);
    expect(step.bargeIn).toBe(true);
    expect(step.send).toHaveLength(2);
  });

  it("forwards the first speech frame after playback ends without gating or barge-in", () => {
    const guard = new EchoGuard(RATE);
    guard.push(frame(0.01), true, 0.1, 0);
    const first = frame(0.4);
    const step = guard.push(first, false, 0, 100);
    expect(step.bargeIn).toBe(false);
    expect(step.send).toEqual([first]);
  });

  it("keeps music from the mini player away from the server's VAD, but lets the user talk over it", () => {
    const guard = new EchoGuard(RATE);
    // Music at the mic: louder than room noise (it would open a turn), quieter than someone talking over it.
    const music = frame(0.05);
    const step = guard.push(music, false, 0, 0, true);
    expect(step.bargeIn).toBe(false);
    expect(step.send).toHaveLength(1);
    expect(step.send[0]).not.toBe(music);
    expect(step.send[0]?.byteLength).toBe(music.byteLength);
    expect(new Int16Array(step.send[0] as ArrayBuffer).every((sample) => sample === 0)).toBe(true);
    // The user speaks up: after 150 ms it goes out with its pre-roll, and keeps going out for a moment after.
    const first = frame(0.4);
    expect(guard.push(first, false, 0, 100, true).send[0]).not.toBe(first);
    const second = frame(0.4);
    const heard = guard.push(second, false, 0, 200, true);
    expect(heard.bargeIn).toBe(false);
    expect(heard.send.slice(-2)).toEqual([first, second]);
    const pause = frame(0.05);
    expect(guard.push(pause, false, 0, 300, true).send).toEqual([pause]);
    expect(guard.push(frame(0.05), false, 0, 1200, true).send[0]).not.toBe(pause);
    // Music stopped: frames pass as they are.
    const plain = frame(0.05);
    expect(guard.push(plain, false, 0, 1300, false).send).toEqual([plain]);
  });
});
