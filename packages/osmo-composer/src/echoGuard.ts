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
 * to interrupt, and sends the held pre-roll so the start of what the user said is not lost.
 *
 * Music from the mini player is not Osmo's voice, but the server's VAD would take it for the user just the same. So
 * while music plays (and Osmo does not), each frame is judged against `musicFloor`: speech loud enough to stand out
 * from the music is sent (with its pre-roll, and for `musicHangMs` after, so words are not chopped), and anything
 * else goes as silence of the same length, which keeps the server's clock and lets it end the user's turn. Outside
 * playback and music frames pass straight through.
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
  /** RMS a block must reach to be the user while music plays. */
  musicFloor: number;
  /** How long after the user was last heard over music frames still pass. */
  musicHangMs: number;
}

/** Favor preserving quiet speech over aggressive echo suppression; provider VAD still decides turn boundaries. */
export const ECHO_GUARD: EchoGuardOptions = { floor: 0.012, ratio: 0.35, blocks: 3, blockMs: 50, tailMs: 0, preroll: 5, musicFloor: 0.045, musicHangMs: 1000 };

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
  // After a barge-in, frames pass until playback has stopped (the caller flushed it).
  private open = false;
  private heardOverMusic = Number.NEGATIVE_INFINITY;

  constructor(
    private readonly sampleRate: number,
    private readonly options: EchoGuardOptions = ECHO_GUARD,
  ) {}

  /**
   * One 100 ms PCM16 frame from the microphone, with whether Osmo's voice is playing, its output RMS right now,
   * the time (ms, any monotonic clock), and whether the music player is playing.
   */
  push(frame: ArrayBuffer, playing: boolean, output: number, now: number, music = false, sileroSpeech?: boolean): GuardStep {
    // Silero only arbitrates local interruption and music suppression. The provider
    // still receives continuous audio while Osmo is otherwise silent.
    if (!playing && sileroSpeech !== undefined) {
      if (!music) return this.pass(frame);
      return this.overMusicSilero(frame, now, sileroSpeech);
    }
    // Never gate audio after playback has stopped: the first quiet words of a new turn must reach provider VAD.
    const guarded = playing;
    if (!guarded && music) return this.overMusic(frame, now);
    if (!guarded || this.open) {
      if (!playing) this.open = false;
      return this.pass(frame);
    }
    // Silero-positive speech is the primary voice detector while playback is active;
    // keep a conservative acoustic-level check against assistant echo. When Silero is
    // unavailable, retain the original RMS fallback rather than stopping the call.
    const options = sileroSpeech === undefined
      ? this.options
      : sileroSpeech
        ? { ...this.options, floor: 0.006, ratio: 0.20, blocks: 1 }
        : { ...this.options, floor: Number.POSITIVE_INFINITY };
    const tripped = this.judge(frame, playing ? output : 0, options);
    if (!tripped) return { send: [], bargeIn: false };
    const send = this.held;
    this.held = [];
    this.run = 0;
    this.open = playing;
    return { send, bargeIn: playing };
  }

  /** Silero music gate: preserve pre-roll and send silence for non-speech music. */
  private overMusicSilero(frame: ArrayBuffer, now: number, speaking: boolean): GuardStep {
    this.open = false;
    if (speaking) {
      this.heardOverMusic = now;
      const send = [...this.held, frame];
      this.held = [];
      return { send, bargeIn: false };
    }
    if (now - this.heardOverMusic < this.options.musicHangMs) {
      return { send: [frame], bargeIn: false };
    }
    this.held.push(frame);
    if (this.held.length > this.options.preroll) this.held.shift();
    return { send: [new ArrayBuffer(frame.byteLength)], bargeIn: false };
  }

  /** Music, no Osmo: the user's speech goes out, everything else as silence. */
  private overMusic(frame: ArrayBuffer, now: number): GuardStep {
    this.open = false;
    if (this.judge(frame, 0, { ...this.options, floor: this.options.musicFloor })) {
      this.heardOverMusic = now;
      const send = this.held;
      this.held = [];
      this.run = 0;
      return { send, bargeIn: false };
    }
    if (now - this.heardOverMusic < this.options.musicHangMs) {
      this.held = [];
      return { send: [frame], bargeIn: false };
    }
    return { send: [new ArrayBuffer(frame.byteLength)], bargeIn: false };
  }

  /** Judge `frame` block by block and hold it as pre-roll; whether the user has now spoken for `blocks` blocks. */
  private judge(frame: ArrayBuffer, output: number, options: EchoGuardOptions): boolean {
    const samples = pcm16ToFloat(frame);
    const size = Math.max(1, Math.round((this.sampleRate * options.blockMs) / 1000));
    let tripped = false;
    for (let start = 0; start < samples.length; start += size) {
      const speech = isUserSpeech(rms(samples, start, Math.min(samples.length, start + size)), output, options);
      this.run = speech ? this.run + 1 : 0;
      if (this.run >= options.blocks) tripped = true;
    }
    this.held.push(frame);
    if (this.held.length > options.preroll) this.held.shift();
    return tripped;
  }

  private pass(frame: ArrayBuffer): GuardStep {
    // Frames held during a tail that never became speech were Osmo's echo: they are dropped, not sent late.
    this.held = [];
    this.run = 0;
    return { send: [frame], bargeIn: false };
  }
}
