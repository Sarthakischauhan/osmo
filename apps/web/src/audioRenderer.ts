import { AUDIO_SAMPLE_RATE, MicCapture, PcmPlayer } from "@osmo/osmo-composer";
import type { AudioBridge } from "./audioBridge";

/** What the hidden audio page does for a call: capture the microphone, play what Osmo says, and drop it on barge-in. */
export interface AudioRuntime {
  /** Opens the microphone; resolves once frames are flowing, rejects with why not (denied, no device). */
  start(onFrame: (pcm16: ArrayBuffer) => void): Promise<void>;
  /** Closes the microphone and drops queued speech. */
  stop(): void;
  enqueue(pcm16: ArrayBuffer): void;
  flush(): void;
}

/** The page's audio, on one 24 kHz context: playing and capturing share it so echo cancellation hears what it plays. */
export function browserRuntime(): AudioRuntime {
  const context = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
  const mic = new MicCapture(context);
  const player = new PcmPlayer(context, () => undefined);
  return {
    async start(onFrame) {
      await context.resume();
      await mic.start(onFrame);
    },
    stop() {
      mic.stop();
      player.flush();
      void context.close();
    },
    enqueue: (pcm16) => player.enqueue(pcm16),
    flush: () => player.flush(),
  };
}

/**
 * The hidden audio renderer of the Electron app: main owns the call and the socket, and asks this page for the
 * microphone and the speakers over the audio bridge. A runtime lasts one call, from `start` to `stop`.
 */
export function runAudioRenderer(bridge: AudioBridge, open: () => AudioRuntime): void {
  let runtime: AudioRuntime | undefined;

  const release = () => {
    const current = runtime;
    runtime = undefined;
    current?.stop();
  };

  bridge.onStart(async () => {
    release();
    const current = open();
    runtime = current;
    try {
      await current.start(bridge.frame);
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
  bridge.ready();
}
