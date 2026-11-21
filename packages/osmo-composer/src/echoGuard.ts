import { pcm16ToFloat } from "./pcm";

/**
 * The echo guard and local barge-in: what keeps Osmo from interrupting itself, and lets the user cut it off fast.
 *
 * While Osmo's voice plays (and for a short tail after, the room's reverb), the microphone mostly hears Osmo:
 * the browser's echo cancellation removes much of it but not all, and the server's VAD would take what is left
 * for the user and cut Osmo off. So while Osmo plays, microphone frames are held back (the last few kept as
 * pre-roll) instead of being sent, and each one is judged here: speech is the user's only when it is loud in its
 * own right (`floor`) and loud next to what Osmo is playing right now (`ratio` of the output's level; echo left
 * after cancellation is far quieter than the output). `blocks` judged blocks in a row of that is a barge-in: the
 * caller stops playback at once (no round trip to the server, whose audio may be seconds ahead), tells the server
 * to interrupt, and sends the held pre-roll so the start of what the user said is not lost. Outside playback
 * frames pass straight through.
 */
export interface EchoGuardOptions {
  /** RMS (0..1, PCM full scale) a block must reach to be speech at all. */
  floor: number;
  /** How loud a block must be next to the output level at that moment. */
  ratio: number;
  /** Consecutive speech blocks (each `blockMs`) that make a barge-in. */
  blocks: number;
  blockMs: number;
  /** How long after playback stops frames are still judged. */
  tailMs: number;
  /** Held frames kept for pre-roll (100 ms each). */
  preroll: number;
}

/** Chosen values: a 50 ms block, three of them (150 ms of speech) to barge in, -34 dBFS floor, user at least 0.6x the output. */
export const ECHO_GUARD: EchoGuardOptions = { floor: 0.02, ratio: 0.6, blocks: 3, blockMs: 50, tailMs: 300, preroll: 5 };

/** Root mean square of samples (0..1). */
export function rms(samples: Float32Array, start = 0, end = samples.length): number {
  if (end <= start) return 0;
  let sum = 0;
  for (let i = start; i < end; i++) {
    const sample = samples[i] ?? 0;
    sum += sample * sample;
  }
  return Math.sqrt(sum / (end - start));
}

/** Whether a block at `mic` RMS is the user rather than Osmo's own voice at `output` RMS coming back. */
export function isUserSpeech(mic: number, output: number, options: EchoGuardOptions = ECHO_GUARD): boolean {
  return mic >= options.floor && mic >= output * options.ratio;
}

/** What to do with one microphone frame: the frames to send now, and whether the user just barged in on Osmo. */
export interface GuardStep {
  send: ArrayBuffer[];
  bargeIn: boolean;
}

export class EchoGuard {
  private held: ArrayBuffer[] = [];
  private run = 0;
  private lastPlayback = Number.NEGATIVE_INFINITY;
  // After a barge-in, frames pass until playback has stopped (the caller flushed it).
  private open = false;

  constructor(
    private readonly sampleRate: number,
    private readonly options: EchoGuardOptions = ECHO_GUARD,
  ) {}

  /**
   * One 100 ms PCM16 frame from the microphone, with whether Osmo's voice is playing, its output RMS right now,
   * and the time (ms, any monotonic clock).
   */
  push(frame: ArrayBuffer, playing: boolean, output: number, now: number): GuardStep {
    if (playing) this.lastPlayback = now;
    const guarded = playing || now - this.lastPlayback < this.options.tailMs;
    if (!guarded || this.open) {
      if (!playing) this.open = false;
      return this.pass(frame);
    }
    const samples = pcm16ToFloat(frame);
    const size = Math.max(1, Math.round((this.sampleRate * this.options.blockMs) / 1000));
    let tripped = false;
    for (let start = 0; start < samples.length; start += size) {
      const speech = isUserSpeech(rms(samples, start, Math.min(samples.length, start + size)), playing ? output : 0, this.options);
      this.run = speech ? this.run + 1 : 0;
      if (this.run >= this.options.blocks) tripped = true;
    }
    this.held.push(frame);
    if (this.held.length > this.options.preroll) this.held.shift();
    if (!tripped) return { send: [], bargeIn: false };
    const send = this.held;
    this.held = [];
    this.run = 0;
    this.open = playing;
    return { send, bargeIn: playing };
  }

  private pass(frame: ArrayBuffer): GuardStep {
    // Frames held during a tail that never became speech were Osmo's echo: they are dropped, not sent late.
    this.held = [];
    this.run = 0;
    return { send: [frame], bargeIn: false };
  }
}
