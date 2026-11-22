import { describe, expect, it } from "vitest";
import { PcmPlayer } from "../src/audio";

/** Just enough of an AudioContext for PcmPlayer: buffers and sources that record that they were started. */
class FakeContext {
  currentTime = 0;
  destination = {};
  started = 0;
  createAnalyser() {
    return { fftSize: 0, smoothingTimeConstant: 0, connect: () => undefined, getFloatTimeDomainData: () => undefined };
  }
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, copyToChannel: () => undefined };
  }
  createBufferSource() {
    return { buffer: null, onended: null, connect: () => undefined, start: () => void this.started++, stop: () => undefined };
  }
}

describe("the PCM player", () => {
  it("after a local barge-in drops the cut-off answer still arriving, until the server's barge-in flushes", () => {
    const context = new FakeContext();
    const player = new PcmPlayer(context as unknown as AudioContext, () => undefined);
    player.enqueue(new ArrayBuffer(960));
    expect(player.playing).toBe(true);
    player.cut();
    expect(player.playing).toBe(false);
    // Late chunks of the answer the user talked over.
    player.enqueue(new ArrayBuffer(960));
    player.enqueue(new ArrayBuffer(960));
    expect(player.playing).toBe(false);
    expect(context.started).toBe(1);
    // The server stopped it and says so (a flush): the next answer plays.
    player.flush();
    player.enqueue(new ArrayBuffer(960));
    expect(player.playing).toBe(true);
    expect(context.started).toBe(2);
  });
});
