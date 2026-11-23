import { MicVAD } from "@ricky0123/vad-web";

/**
 * Local speech evidence for interruption, not transcription or provider turn detection.
 * The same already-permissioned microphone stream is shared with Osmo's PCM capture.
 * A VAD failure must never prevent or mute the main microphone stream.
 */
export class SileroVad {
  private detector: MicVAD | undefined;
  private recentUntil = 0;
  private disposed = false;
  private initialized = false;

  get ready(): boolean {
    return this.initialized && !this.disposed;
  }

  /** Recent Silero-positive speech, sampled by the existing 100 ms audio pipeline. */
  speaking(now: number): boolean {
    return this.ready && now < this.recentUntil;
  }

  async start(stream: MediaStream): Promise<void> {
    try {
      const detector = await MicVAD.new({
        model: "v5",
        baseAssetPath: "/vad/",
        onnxWASMBasePath: "/vad/",
        getStream: async () => stream,
        // MicCapture owns this stream and its permissions. Never stop or reacquire it here.
        pauseStream: async () => {},
        resumeStream: async () => stream,
        positiveSpeechThreshold: 0.45,
        negativeSpeechThreshold: 0.30,
        minSpeechMs: 160,
        redemptionMs: 350,
        onFrameProcessed: ({ isSpeech }) => {
          if (isSpeech >= 0.45 && !this.disposed) {
            // Keep evidence across model's ~32 ms frames and Osmo's 100 ms PCM chunks.
            this.recentUntil = performance.now() + 250;
          }
        },
      });
      if (this.disposed) {
        detector.pause();
        return;
      }
      this.detector = detector;
      detector.start();
      this.initialized = true;
    } catch (error) {
      // Keep provider server-VAD and the existing RMS fallback operational.
      console.warn("[osmo vad] Silero unavailable; using RMS fallback", error);
    }
  }

  stop(): void {
    this.disposed = true;
    this.recentUntil = 0;
    this.initialized = false;
    this.detector?.pause();
    this.detector = undefined;
  }
}
