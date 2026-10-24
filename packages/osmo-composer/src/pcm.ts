/** PCM16 little-endian <-> Float32 conversion for 24 kHz mono audio frames. */

export function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const out = new DataView(new ArrayBuffer(samples.length * 2));
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    out.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return out.buffer;
}

export function pcm16ToFloat(bytes: ArrayBuffer): Float32Array<ArrayBuffer> {
  const view = new DataView(bytes);
  const out = new Float32Array(Math.floor(bytes.byteLength / 2));
  for (let i = 0; i < out.length; i++) {
    out[i] = view.getInt16(i * 2, true) / 0x8000;
  }
  return out;
}

/** Collects mic samples and yields fixed-size chunks (100 ms at 24 kHz by default). */
export class SampleBatcher {
  private buffer: Float32Array;
  private filled = 0;

  constructor(readonly size = 2400) {
    this.buffer = new Float32Array(size);
  }

  push(samples: Float32Array): Float32Array[] {
    const ready: Float32Array[] = [];
    let offset = 0;
    while (offset < samples.length) {
      const take = Math.min(this.size - this.filled, samples.length - offset);
      this.buffer.set(samples.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.size) {
        ready.push(this.buffer);
        this.buffer = new Float32Array(this.size);
        this.filled = 0;
      }
    }
    return ready;
  }
}
