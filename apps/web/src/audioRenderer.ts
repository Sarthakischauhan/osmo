import { AUDIO_SAMPLE_RATE, EchoGuard, MicCapture, PcmPlayer } from "@osmo/osmo-composer";
import type { AudioBridge } from "./audioBridge";

/** What the hidden audio page does for a call: capture the microphone, play what Osmo says, and drop it on barge-in. */
export interface AudioRuntime {
  /**
   * Opens the microphone; resolves once frames are flowing, rejects with why not (denied, no device). Frames pass the
   * echo guard: while Osmo plays they are held back, and `onBargeIn` says the user talked over it (playback is already cut).
   */
  start(onFrame: (pcm16: ArrayBuffer) => void, onBargeIn: () => void): Promise<void>;
  /** Closes the microphone and drops queued speech. */
  stop(): void;
  enqueue(pcm16: ArrayBuffer): void;
  flush(): void;
  /** Whether the workspace's music player is playing (the echo guard keeps it out of the server's VAD). */
  music(playing: boolean): void;
}

/** The page's audio, on one 24 kHz context: playing and capturing share it so echo cancellation hears what it plays. */
export function browserRuntime(): AudioRuntime {
  const context = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
  const mic = new MicCapture(context);
  const player = new PcmPlayer(context, () => undefined);
  const guard = new EchoGuard(AUDIO_SAMPLE_RATE);
  let music = false;
  const debugAudio = new URLSearchParams(window.location.search).has("debugAudio");
  let captured = 0;
  let forwarded = 0;
  let guarded = 0;
  let bargeIns = 0;
  return {
    async start(onFrame, onBargeIn) {
      await context.resume();
      await mic.start((frame) => {
        const step = guard.push(frame, player.playing, player.rms(), performance.now(), music);
        captured++;
        forwarded += step.send.length;
        if (step.send.length === 0) guarded++;
        if (step.bargeIn) bargeIns++;
        if (debugAudio && captured % 50 === 0) {
          console.info("[osmo audio] desktop capture/forwarded/held/bargeIns", { captured, forwarded, guarded, bargeIns, playing: player.playing, music });
        }
        if (step.bargeIn) {
          // Cut Osmo off here and now, before main and the server hear about it; the rest of that answer still on its
          // way is dropped until main passes on the server's barge-in (a flush).
          player.cut();
          onBargeIn();
        }
        for (const held of step.send) onFrame(held);
      });
    },
    stop() {
      mic.stop();
      player.flush();
      void context.close();
    },
    enqueue: (pcm16) => player.enqueue(pcm16),
    flush: () => player.flush(),
    music: (playing) => {
      music = playing;
    },
  };
}

/**
 * The hidden audio renderer of the Electron app: main owns the call and the socket, and asks this page for the
 * microphone and the speakers over the audio bridge. A runtime lasts one call, from `start` to `stop`.
 */
export function runAudioRenderer(bridge: AudioBridge, open: () => AudioRuntime): void {
  let runtime: AudioRuntime | undefined;
  // The music player's last word, kept for the next call's runtime.
  let music = false;

  const release = () => {
    const current = runtime;
    runtime = undefined;
    current?.stop();
  };

  bridge.onStart(async () => {
    release();
    const current = open();
    runtime = current;
    // A runtime starts with no music; tell it only when there is.
    if (music) current.music(true);
    try {
      await current.start(bridge.frame, bridge.bargeIn);
    } catch (cause) {
      // Only the newest start is answered: a replaced one belongs to a call main has already moved on from.
      if (runtime !== current) return;
      release();
      bridge.started({ error: cause instanceof Error ? cause.message : String(cause) });
      return;
    }
    // Stopped or replaced while the microphone was opening: it opened for a call that is over, so close it again.
    if (runtime !== current) current.stop();
    else bridge.started({});
  });
  bridge.onStop(release);
  bridge.onPlay((pcm16) => runtime?.enqueue(pcm16));
  bridge.onFlush(() => runtime?.flush());
  bridge.onMusic((playing) => {
    music = playing;
    runtime?.music(playing);
  });
  bridge.ready();
}
