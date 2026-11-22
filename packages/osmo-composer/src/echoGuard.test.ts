import { describe, expect, it } from "vitest";
import { EchoGuard } from "./echoGuard";

const RATE = 24_000;
function frame(amplitude: number): ArrayBuffer {
  const samples = new Int16Array(RATE / 10);
  samples.fill(Math.round(amplitude * 32767));
  return samples.buffer;
}

describe("EchoGuard speech preservation", () => {
  it("forwards quiet speech immediately after playback stops", () => {
    const guard = new EchoGuard(RATE);
    guard.push(frame(0.001), true, 0.2, 1000);
    const result = guard.push(frame(0.008), false, 0, 1100);
    expect(result.bargeIn).toBe(false);
    expect(result.send).toHaveLength(1);
  });

  it("recognizes quieter barge-in and retains its start", () => {
    const guard = new EchoGuard(RATE);
    const speech = frame(0.025);
    const result = guard.push(speech, true, 0.05, 1000);
    expect(result.bargeIn).toBe(true);
    expect(result.send).toHaveLength(1);
    expect(result.send[0]).toBe(speech);
  });

  it("does not forward low-level playback echo as user speech", () => {
    const guard = new EchoGuard(RATE);
    expect(guard.push(frame(0.001), true, 0.2, 1000).send).toHaveLength(0);
  });

  it("passes speech over music with retained preroll", () => {
    const guard = new EchoGuard(RATE);
    const speech = frame(0.04);
    const result = guard.push(speech, false, 0, 1000, true);
    expect(result.send).toHaveLength(1);
    expect(result.send[0]).toBe(speech);
  });
});
