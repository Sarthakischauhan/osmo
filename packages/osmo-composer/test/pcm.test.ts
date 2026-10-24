import { describe, expect, it } from "vitest";
import { SampleBatcher, floatToPcm16, pcm16ToFloat } from "../src/pcm";

describe("pcm", () => {
  it("round-trips samples within PCM16 precision and clips", () => {
    const input = new Float32Array([0, 0.5, -0.5, 1, -1, 2]);
    const output = pcm16ToFloat(floatToPcm16(input));
    expect(output.length).toBe(6);
    for (const [i, expected] of [0, 0.5, -0.5, 1, -1, 1].entries()) {
      expect(output[i]).toBeCloseTo(expected, 3);
    }
  });

  it("writes little-endian int16", () => {
    const bytes = new Uint8Array(floatToPcm16(new Float32Array([-1])));
    expect([...bytes]).toEqual([0x00, 0x80]);
  });

  it("batches arbitrary chunks into fixed frames", () => {
    const batcher = new SampleBatcher(4);
    expect(batcher.push(new Float32Array(3))).toHaveLength(0);
    const frames = batcher.push(new Float32Array([1, 2, 3, 4, 5, 6]));
    expect(frames.map((f) => [...f])).toEqual([[0, 0, 0, 1], [2, 3, 4, 5]]);
  });
});
