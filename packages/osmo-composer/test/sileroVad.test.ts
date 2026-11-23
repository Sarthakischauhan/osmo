import { describe, expect, it, vi } from "vitest";

const pause = vi.fn();
const start = vi.fn();
let options: Record<string, unknown> = {};
vi.mock("@ricky0123/vad-web", () => ({
  MicVAD: { new: async (value: Record<string, unknown>) => {
    options = value;
    return { start, pause };
  } },
}));

import { SileroVad } from "../src/sileroVad";

describe("Silero lifecycle", () => {
  it("shares the existing mic and never owns its tracks", async () => {
    const detector = new SileroVad();
    const stream = {} as MediaStream;
    await detector.start(stream);
    expect(await (options.getStream as () => Promise<MediaStream>)()).toBe(stream);
    expect(options.model).toBe("v5");
    expect(options.baseAssetPath).toBe("/vad/");
    expect(detector.ready).toBe(true);
    expect(start).toHaveBeenCalled();
    await (options.pauseStream as () => Promise<void>)();
    detector.stop();
    expect(pause).toHaveBeenCalled();
    expect(detector.ready).toBe(false);
  });

  it("provides recent speech evidence without gating ordinary capture", async () => {
    const detector = new SileroVad();
    await detector.start({} as MediaStream);
    const clock = vi.spyOn(performance, "now").mockReturnValue(1000);
    try {
      (options.onFrameProcessed as (p: {isSpeech: number}) => void)({ isSpeech: 0.6 });
      expect(detector.speaking(1100)).toBe(true);
      expect(detector.speaking(1300)).toBe(false);
      detector.stop();
      expect(detector.speaking(1100)).toBe(false);
    } finally { clock.mockRestore(); }
  });
});
