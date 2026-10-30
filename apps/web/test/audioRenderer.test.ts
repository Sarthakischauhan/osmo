import { describe, expect, it } from "vitest";
import type { AudioBridge } from "../src/audioBridge";
import { type AudioRuntime, runAudioRenderer } from "../src/audioRenderer";

type Callbacks = { start?: () => void; stop?: () => void; play?: (pcm: ArrayBuffer) => void; flush?: () => void };

/** A bridge the test drives as main would, recording what the page sends back. */
function setup() {
  const sent: unknown[] = [];
  const callbacks: Callbacks = {};
  const bridge: AudioBridge = {
    ready: () => void sent.push("ready"),
    started: (result) => void sent.push(["started", result]),
    frame: (pcm16) => void sent.push(["frame", pcm16.byteLength]),
    onStart: (callback) => ((callbacks.start = callback), () => undefined),
    onStop: (callback) => ((callbacks.stop = callback), () => undefined),
    onPlay: (callback) => ((callbacks.play = callback), () => undefined),
    onFlush: (callback) => ((callbacks.flush = callback), () => undefined),
  };
  const runtimes: FakeRuntime[] = [];
  const open = () => {
    const runtime = new FakeRuntime();
    runtimes.push(runtime);
    return runtime;
  };
  runAudioRenderer(bridge, open);
  return { sent, callbacks, runtimes };
}

class FakeRuntime implements AudioRuntime {
  log: string[] = [];
  onFrame: ((pcm16: ArrayBuffer) => void) | undefined;
  opened: { resolve: () => void; reject: (error: Error) => void } | undefined;
  start(onFrame: (pcm16: ArrayBuffer) => void) {
    this.log.push("start");
    this.onFrame = onFrame;
    return new Promise<void>((resolve, reject) => (this.opened = { resolve, reject }));
  }
  stop() {
    this.log.push("stop");
  }
  enqueue(pcm16: ArrayBuffer) {
    this.log.push(`play ${pcm16.byteLength}`);
  }
  flush() {
    this.log.push("flush");
  }
}

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("the hidden audio renderer", () => {
  it("tells main it is up once it is listening", () => {
    expect(setup().sent).toEqual(["ready"]);
  });

  it("opens the microphone on request, sends its frames to main, and says when it is open", async () => {
    const { sent, callbacks, runtimes } = setup();
    callbacks.start?.();
    runtimes[0]?.opened?.resolve();
    await settled();
    runtimes[0]?.onFrame?.(new ArrayBuffer(4800));
    runtimes[0]?.onFrame?.(new ArrayBuffer(4800));
    expect(sent).toEqual(["ready", ["started", {}], ["frame", 4800], ["frame", 4800]]);
  });

  it("reports why the microphone could not open, and holds nothing", async () => {
    const { sent, callbacks, runtimes } = setup();
    callbacks.start?.();
    runtimes[0]?.opened?.reject(new Error("Permission denied"));
    await settled();
    expect(sent).toEqual(["ready", ["started", { error: "Permission denied" }]]);
    expect(runtimes[0]?.log).toEqual(["start", "stop"]);
    callbacks.play?.(new ArrayBuffer(8));
    expect(runtimes[0]?.log).toEqual(["start", "stop"]);
  });

  it("plays what main sends and drops it on a flush", async () => {
    const { callbacks, runtimes } = setup();
    callbacks.start?.();
    runtimes[0]?.opened?.resolve();
    await settled();
    callbacks.play?.(new ArrayBuffer(960));
    callbacks.flush?.();
    callbacks.play?.(new ArrayBuffer(480));
    expect(runtimes[0]?.log).toEqual(["start", "play 960", "flush", "play 480"]);
  });

  it("closes the microphone when main stops the call, and ignores speech after it", async () => {
    const { callbacks, runtimes } = setup();
    callbacks.start?.();
    runtimes[0]?.opened?.resolve();
    await settled();
    callbacks.stop?.();
    callbacks.play?.(new ArrayBuffer(960));
    callbacks.flush?.();
    expect(runtimes[0]?.log).toEqual(["start", "stop"]);
  });

  it("closes a microphone that finished opening after the call was stopped, and does not answer for it", async () => {
    const { sent, callbacks, runtimes } = setup();
    callbacks.start?.();
    callbacks.stop?.();
    runtimes[0]?.opened?.resolve();
    await settled();
    expect(runtimes[0]?.log).toEqual(["start", "stop", "stop"]);
    expect(sent).toEqual(["ready"]);
  });

  it("answers only the newest start when a second arrives before the first has opened", async () => {
    const { sent, callbacks, runtimes } = setup();
    callbacks.start?.();
    callbacks.start?.();
    runtimes[0]?.opened?.reject(new Error("superseded"));
    runtimes[1]?.opened?.resolve();
    await settled();
    expect(runtimes[0]?.log).toEqual(["start", "stop"]);
    expect(sent).toEqual(["ready", ["started", {}]]);
  });
});
