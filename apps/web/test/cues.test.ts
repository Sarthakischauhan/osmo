import { beforeEach, describe, expect, it, vi } from "vitest";

const play = vi.fn();
const unlock = vi.fn(async () => true);
vi.mock("cuelume", () => ({ play, unlock }));

const { CUES, Cues } = await import("../src/cues");

const options = { prompt: "Pick", choices: [{ id: "a", label: "A" }, { id: "b", label: "B" }] };
const sounds = () => play.mock.calls.map((call) => call[0]);

async function ready(speaking = { now: false }) {
  const cues = new Cues(() => speaking.now);
  await cues.gesture();
  return cues;
}

describe("Cues", () => {
  beforeEach(() => {
    play.mockClear();
    unlock.mockClear();
  });

  it("maps events to the documented cue names", () => {
    expect(CUES).toEqual({
      optionPick: "select",
      confirm: "success",
      widgetAppear: "ready",
      sessionStart: "open",
      sessionEnd: "close",
      awaitingChoice: "attention",
    });
  });

  it("stays silent until a user gesture unlocks audio", async () => {
    const cues = new Cues(() => false);
    cues.onConfirm();
    expect(play).not.toHaveBeenCalled();
    await cues.gesture();
    cues.onConfirm();
    expect(sounds()).toEqual(["success"]);
    expect(unlock).toHaveBeenCalledTimes(1);
  });

  it("stays silent if the browser still blocks audio", async () => {
    unlock.mockResolvedValueOnce(false);
    const cues = new Cues(() => false);
    await cues.gesture();
    cues.onOptionPick();
    expect(play).not.toHaveBeenCalled();
  });

  it("plays select on pick and success on confirm, quietly", async () => {
    const cues = await ready();
    cues.onOptionPick();
    cues.onConfirm();
    expect(sounds()).toEqual(["select", "success"]);
    for (const call of play.mock.calls) expect(call[1].volume).toBeLessThanOrEqual(0.35);
  });

  it("plays open on connect and close only when a live session ends", async () => {
    const cues = await ready();
    cues.onConnection("connecting");
    cues.onConnection("disconnected");
    expect(play).not.toHaveBeenCalled();
    cues.onConnection("connecting");
    cues.onConnection("connected");
    cues.onConnection("connected");
    cues.onConnection("disconnected");
    expect(sounds()).toEqual(["open", "close"]);
  });

  it("plays ready for plain widgets, not for choices", async () => {
    const cues = await ready();
    cues.onServerEvent({ type: "widget.render", widget: { id: "c1", type: "card", props: { title: "t", body: "b" } } });
    cues.onServerEvent({ type: "widget.render", widget: { id: "o1", type: "options", props: options } });
    cues.onServerEvent({ type: "widget.update", id: "c1", patch: { title: "x" } });
    expect(sounds()).toEqual(["ready"]);
  });

  it("rings attention once per pending choice", async () => {
    const cues = await ready();
    cues.onAmber("o1");
    cues.onAmber(undefined);
    cues.onAmber("o1");
    cues.onAmber("o2");
    expect(sounds()).toEqual(["attention", "attention"]);
  });

  it("keeps passive cues off the assistant's speech but not the user's own actions", async () => {
    const speaking = { now: true };
    const cues = await ready(speaking);
    cues.onServerEvent({ type: "widget.render", widget: { id: "c1", type: "card", props: { title: "t", body: "b" } } });
    cues.onAmber("o1");
    cues.onConfirm();
    expect(sounds()).toEqual(["success"]);
  });
});
