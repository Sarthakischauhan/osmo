import { AUDIO_SAMPLE_RATE } from "./protocol";
import { rms } from "./echoGuard";
import { SampleBatcher, floatToPcm16, pcm16ToFloat } from "./pcm";

const WORKLET = `
class OsmoMicTap extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("osmo-mic-tap", OsmoMicTap);
`;

/** Mic capture at 24 kHz, delivered as 100 ms PCM16 frames. */
export class MicCapture {
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  readonly analyser: AnalyserNode;

  constructor(private readonly context: AudioContext) {
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.45;
  }

  async start(onFrame: (pcm: ArrayBuffer) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.context.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    this.source = this.context.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.context, "osmo-mic-tap");
    const batcher = new SampleBatcher();
    this.node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      for (const chunk of batcher.push(event.data)) onFrame(floatToPcm16(chunk));
    };
    this.source.connect(this.node);
    // The analyser is only read for the orb; it is not connected to the speakers.
    this.source.connect(this.analyser);
  }

  private readonly samples = new Float32Array(256);

  /** Mic loudness right now, 0..1. Zero until capture has started. */
  level(): number {
    if (!this.source) return 0;
    return loudness(this.analyser, this.samples);
  }

  stop(): void {
    this.node?.port.close();
    this.node?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.node = null;
    this.source = null;
    this.stream = null;
  }
}

/** RMS of an analyser's time domain, scaled so speech lands mid-range. */
export function loudness(analyser: AnalyserNode, samples: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(samples);
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

/** Gapless PCM16 playback with an instant flush for barge-in. */
export class PcmPlayer {
  private nextTime = 0;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private remainder: Uint8Array | null = null;
  readonly analyser: AnalyserNode;

  constructor(
    private readonly context: AudioContext,
    private readonly onIdle: () => void,
  ) {
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.45;
    this.analyser.connect(context.destination);
  }

  private readonly samples = new Float32Array(256);

  /** Output loudness right now, 0..1 (RMS of what is playing, scaled so speech lands mid-range). */
  level(): number {
    return loudness(this.analyser, this.samples);
  }

  /** The plain RMS (PCM full scale) of what is playing right now: what the echo guard weighs the microphone against. */
  rms(): number {
    this.analyser.getFloatTimeDomainData(this.samples);
    return rms(this.samples);
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  enqueue(frame: ArrayBuffer): void {
    let bytes = new Uint8Array(frame);
    if (this.remainder) {
      const joined = new Uint8Array(this.remainder.length + bytes.length);
      joined.set(this.remainder);
      joined.set(bytes, this.remainder.length);
      bytes = joined;
      this.remainder = null;
    }
    if (bytes.length % 2 === 1) {
      this.remainder = bytes.slice(-1);
      bytes = bytes.slice(0, -1);
    }
    if (bytes.length === 0) return;
    const samples = pcm16ToFloat(bytes.slice().buffer);
    const buffer = this.context.createBuffer(1, samples.length, AUDIO_SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    const at = Math.max(this.nextTime, this.context.currentTime + 0.02);
    source.start(at);
    this.nextTime = at + buffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      if (this.sources.size === 0) this.onIdle();
    };
  }

  /** Stop everything queued, right now. */
  flush(): void {
    for (const source of this.sources) {
      source.onended = null;
      source.stop();
    }
    this.sources.clear();
    this.remainder = null;
    this.nextTime = 0;
    this.onIdle();
  }
}
