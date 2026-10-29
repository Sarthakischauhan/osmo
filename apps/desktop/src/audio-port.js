// Main's audio, over IPC: the hidden audio renderer (the only page with a microphone and speakers) captures and plays,
// and this is the `AudioPort` the session controller uses. Nothing here touches Electron, so the contract is testable:
// main passes `send` (to the audio renderer) and routes the renderer's messages to `ready`, `captured` and `frame`.

import { CHANNELS } from './session-contract.js';

export const AUDIO_NOT_READY = 'The audio renderer is not running yet. Try again in a moment.';
const CAPTURE_STOPPED = 'Microphone capture stopped before it started';

/**
 * @param {(channel: string, payload?: ArrayBuffer | Uint8Array) => void} send to the audio renderer
 */
export function createAudioPort(send) {
  let ready = false;
  /** @type {((pcm16: ArrayBuffer | Uint8Array) => void) | undefined} */
  let onFrame;
  /** @type {{ resolve: () => void, reject: (error: Error) => void } | undefined} */
  let starting;

  const settle = error => {
    const pending = starting;
    starting = undefined;
    if (!pending) return;
    if (error) pending.reject(error); else pending.resolve();
  };

  return {
    /** @type {import('./session-controller.js').AudioPort} */
    port: {
      start(callback) {
        if (!ready) return Promise.reject(new Error(AUDIO_NOT_READY));
        onFrame = callback;
        return new Promise((resolve, reject) => {
          starting = { resolve, reject };
          send(CHANNELS.audioStart);
        });
      },
      stop() {
        onFrame = undefined;
        settle(new Error(CAPTURE_STOPPED));
        send(CHANNELS.audioStop);
      },
      play: pcm16 => send(CHANNELS.audioPlay, pcm16),
      flush: () => send(CHANNELS.audioFlush),
    },
    /** The audio renderer's page is up and listening. */
    ready() { ready = true; },
    /**
     * The audio renderer answered `audioStart`.
     * @param {{ error?: string }} result
     */
    captured({ error }) {
      if (!starting) return;
      if (error !== undefined) onFrame = undefined;
      settle(error === undefined ? undefined : new Error(error));
    },
    /** A microphone frame from the audio renderer; dropped unless a capture is wanted. */
    frame(pcm16) { onFrame?.(pcm16); },
    /** The audio renderer went away: nothing is being captured, and a pending start fails. */
    lost() {
      ready = false;
      onFrame = undefined;
      settle(new Error('The audio renderer stopped'));
    },
  };
}
