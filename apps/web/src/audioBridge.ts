/** The hidden audio renderer's side of the Electron app's IPC contract (apps/desktop/src/session-contract.js). */
export interface AudioBridge {
  /** This page is up and listening. */
  ready(): void;
  /** The answer to a start request: the microphone is open (`{}`), or why it could not be. */
  started(result: { error?: string }): void;
  /** One 100 ms frame of what the microphone hears: 24 kHz mono PCM16. */
  frame(pcm16: ArrayBuffer): void;
  /** Main wants the microphone open. */
  onStart(callback: () => void): () => void;
  onStop(callback: () => void): () => void;
  /** Assistant speech to queue for playback. */
  onPlay(callback: (pcm16: ArrayBuffer) => void): () => void;
  /** Drop everything queued for playback (barge-in). */
  onFlush(callback: () => void): () => void;
}

declare global { interface Window { osmoAudio?: AudioBridge } }
