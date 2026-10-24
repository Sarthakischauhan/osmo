import { describe, expect, it } from "vitest";
import type { VoiceModel } from "../src/protocol";
import {
  BANDS,
  FREEZE_AFTER_SNAP_MS,
  RINGS,
  clampOffset,
  edgeFalloff,
  hitTest,
  itemAt,
  moreMarkers,
  nearestSlot,
  offsetFor,
  polar,
  slotAngle,
  snapOffset,
} from "../src/radial-picker/geometry";
import { PICK_STORAGE_KEY, defaultPick, restorePick } from "../src/radial-picker/persistence";
import { effortsOf, groupProviders, initialPicker, isFrozen, pickModel, reducePicker } from "../src/radial-picker/reducer";

const voice = (id: string) => ({ id, label: id.toUpperCase(), traits: [] });
const model = (provider: string, id: string, voices: string[], reasoningEffort?: VoiceModel["capabilities"]["reasoningEffort"]): VoiceModel => ({
  id,
  provider,
  label: id,
  capabilities: reasoningEffort ? { tools: true, interrupt: true, customVoices: false, reasoningEffort } : { tools: true, interrupt: true, customVoices: false },
  voices: voices.map(voice),
  defaultVoice: voices[0] ?? "",
});

const REGISTRY: VoiceModel[] = [
  model("xai", "grok-voice-latest", ["eve", "ara", "sal"]),
  ...Array.from({ length: 9 }, (_, i) => model("openai", `m${i}`, ["a", "b"], i === 4 ? ["high", "low"] : undefined)),
  model("acme", "deep", ["v1", "v2", "v3", "v4", "v5", "v6"], ["low", "medium", "high"]),
];
const groups = groupProviders(REGISTRY);
const value = { model: "grok-voice-latest", voice: "eve" };

describe("slot math", () => {
  it("places slots along each arc as specified", () => {
    expect([0, 1, 2, 3, 4].map((p) => slotAngle(RINGS.providers, p))).toEqual([150, 120, 90, 60, 30]);
    expect([0, 1, 2, 3, 4].map((p) => slotAngle(RINGS.models, p))).toEqual([160, 125, 90, 55, 20]);
    expect([0, 1, 2].map((p) => slotAngle(RINGS.voices, p))).toEqual([170, 150, 130]);
    expect([0, 1, 2].map((p) => slotAngle(RINGS.effort, p))).toEqual([46, 30, 14]);
  });
  it("converts angles CCW from +x with screen y down", () => {
    const top = polar(100, 100, 80, 90);
    expect(top.x).toBeCloseTo(100);
    expect(top.y).toBeCloseTo(20);
    const right = polar(100, 100, 80, 0);
    expect(right.x).toBeCloseTo(180);
    expect(right.y).toBeCloseTo(100);
  });
  it("finds the slot nearest an angle", () => {
    expect(nearestSlot(RINGS.models, 150)).toBe(0);
    expect(nearestSlot(RINGS.models, 90)).toBe(2);
    expect(nearestSlot(RINGS.models, 0)).toBe(4);
  });
});

describe("clamp and snap", () => {
  it("clamps both ends without wrapping", () => {
    expect(clampOffset(RINGS.models, 9, -3)).toBe(0);
    expect(clampOffset(RINGS.models, 9, 7.5)).toBe(4);
    expect(clampOffset(RINGS.models, 9, 2.3)).toBe(2.3);
  });
  it("lets a short ring slide but not past its items", () => {
    expect(clampOffset(RINGS.providers, 3, -5)).toBe(-2);
    expect(clampOffset(RINGS.providers, 3, 1)).toBe(0);
  });
  it("snaps to the nearest whole slot, clamped", () => {
    expect(snapOffset(RINGS.models, 9, 2.4)).toBe(2);
    expect(snapOffset(RINGS.models, 9, 2.6)).toBe(3);
    expect(snapOffset(RINGS.models, 9, 4.4)).toBe(4);
  });
  it("centres the window on an anchor", () => {
    expect(offsetFor(RINGS.models, 9, 6)).toBe(4);
    expect(offsetFor(RINGS.models, 9, 0)).toBe(0);
    expect(offsetFor(RINGS.providers, 3, 0)).toBe(-2);
  });
});

describe("edges", () => {
  it("fades over the last half-slot past each end", () => {
    expect(edgeFalloff(RINGS.models, 0)).toBe(1);
    expect(edgeFalloff(RINGS.models, 4)).toBe(1);
    expect(edgeFalloff(RINGS.models, -0.25)).toBeCloseTo(0.5);
    expect(edgeFalloff(RINGS.models, 4.25)).toBeCloseTo(0.5);
    expect(edgeFalloff(RINGS.models, -0.5)).toBe(0);
    expect(edgeFalloff(RINGS.models, 6)).toBe(0);
  });
  it("marks more items past an end", () => {
    expect(moreMarkers(RINGS.models, 9, 0)).toEqual({ before: false, after: true });
    expect(moreMarkers(RINGS.models, 9, 2)).toEqual({ before: true, after: true });
    expect(moreMarkers(RINGS.models, 9, 4)).toEqual({ before: true, after: false });
    expect(moreMarkers(RINGS.voices, 3, 0)).toEqual({ before: false, after: false });
  });
});

describe("hit bands", () => {
  const at = (r: number, angle: number) => polar(0, 0, r, angle);
  it("maps radius to rings and R3 side by angle", () => {
    const ring = (r: number, angle: number) => hitTest(0, 0, at(r, angle).x, at(r, angle).y).ring;
    expect(ring(30, 90)).toBe("orb");
    expect(ring(BANDS.orb, 90)).toBe("providers");
    expect(ring(119, 90)).toBe("providers");
    expect(ring(120, 90)).toBe("models");
    expect(ring(197, 90)).toBe("models");
    expect(ring(198, 120)).toBe("voices");
    expect(ring(274, 60)).toBe("effort");
    expect(ring(275, 60)).toBe("none");
    expect(hitTest(0, 0, 0, 100).ring).toBe("none");
  });
  it("finds the item under an angle at an offset", () => {
    expect(itemAt(RINGS.models, 9, 0, 160)).toBe(0);
    expect(itemAt(RINGS.models, 9, 2, 160)).toBe(2);
    expect(itemAt(RINGS.models, 9, 0, 90)).toBe(2);
  });
});

describe("reducer", () => {
  const open = reducePicker(initialPicker, { type: "open", groups, value });
  it("opens rings in order and keeps the selected path", () => {
    expect(open.depth).toBe(1);
    const r2 = reducePicker(open, { type: "hoverProvider", provider: "openai", now: 0, groups, value });
    expect(r2.depth).toBe(2);
    expect(r2.provider).toBe("openai");
    const r3 = reducePicker(r2, { type: "hoverModel", model: "m4", now: 0, groups, value });
    expect(r3.depth).toBe(3);
    expect(reducePicker(r3, { type: "close" })).toEqual(initialPicker);
  });
  it("ignores model hover before R2 is open", () => {
    expect(reducePicker(open, { type: "hoverModel", model: "m1", now: 0, groups, value }).depth).toBe(1);
  });
  it("centres R2 on the selected model", () => {
    const picked = { model: "m7", voice: "a" };
    const opened = reducePicker(initialPicker, { type: "open", groups, value: picked });
    const r2 = reducePicker(opened, { type: "hoverProvider", provider: "openai", now: 0, groups, value: picked });
    expect(r2.offsets.models).toBe(4);
  });
  it("freezes outer rings while a ring scrolls and for 150 ms after it settles", () => {
    let s = reducePicker(open, { type: "hoverProvider", provider: "openai", now: 0, groups, value });
    s = reducePicker(s, { type: "scroll", ring: "models", slots: 1.4, count: 9 });
    expect(s.offsets.models).toBeCloseTo(1.4);
    expect(isFrozen(s, "models", 10)).toBe(true);
    expect(isFrozen(s, "providers", 10)).toBe(false);
    expect(reducePicker(s, { type: "hoverModel", model: "m1", now: 10, groups, value }).depth).toBe(2);
    s = reducePicker(s, { type: "snap", ring: "models", count: 9 });
    expect(s.offsets.models).toBe(1);
    s = reducePicker(s, { type: "settle", ring: "models", now: 1000 });
    expect(isFrozen(s, "models", 1000 + FREEZE_AFTER_SNAP_MS - 1)).toBe(true);
    expect(reducePicker(s, { type: "hoverModel", model: "m1", now: 1100, groups, value }).depth).toBe(2);
    expect(isFrozen(s, "models", 1000 + FREEZE_AFTER_SNAP_MS)).toBe(false);
    expect(reducePicker(s, { type: "hoverModel", model: "m1", now: 1150, groups, value }).depth).toBe(3);
    expect(reducePicker(s, { type: "thaw", now: 1149 }).scroll).toBeDefined();
    expect(reducePicker(s, { type: "thaw", now: 1150 }).scroll).toBeUndefined();
  });
  it("scrolling R1 freezes R2 switching too", () => {
    const s = reducePicker(open, { type: "scroll", ring: "providers", slots: 0.3, count: 3 });
    expect(reducePicker(s, { type: "hoverProvider", provider: "openai", now: 0, groups, value }).depth).toBe(1);
  });
  it("reveals a keyboard-focused item by scrolling the least", () => {
    const s = { ...open, offsets: { ...open.offsets, models: 0 } };
    expect(reducePicker(s, { type: "reveal", ring: "models", index: 6, count: 9 }).offsets.models).toBe(2);
    expect(reducePicker(s, { type: "reveal", ring: "models", index: 3, count: 9 }).offsets.models).toBe(0);
  });
});

describe("selection rules", () => {
  it("picking a model picks its default voice and keeps an allowed effort", () => {
    const deep = REGISTRY.find((m) => m.id === "deep");
    if (!deep) throw new Error("fixture");
    expect(pickModel(deep, value)).toEqual({ model: "deep", voice: "v1", effort: "medium" });
    expect(pickModel(deep, { ...value, effort: "high" })).toEqual({ model: "deep", voice: "v1", effort: "high" });
    const grok = REGISTRY[0];
    if (!grok) throw new Error("fixture");
    expect(pickModel(grok, { model: "deep", voice: "v1", effort: "high" })).toEqual({ model: "grok-voice-latest", voice: "eve" });
  });
  it("orders efforts low → high and hides them when absent", () => {
    expect(effortsOf(REGISTRY.find((m) => m.id === "m4"))).toEqual(["low", "high"]);
    expect(effortsOf(REGISTRY[0])).toEqual([]);
  });
});

describe("persistence", () => {
  it("uses the key osmo.voice-pick", () => expect(PICK_STORAGE_KEY).toBe("osmo.voice-pick"));
  it("restores a saved pick that still exists", () => {
    expect(restorePick(JSON.stringify({ model: "deep", voice: "v3", effort: "low" }), REGISTRY)).toEqual({ model: "deep", voice: "v3", effort: "low" });
  });
  it("falls back to the registry default when the model is gone or the entry is junk", () => {
    expect(defaultPick(REGISTRY)).toEqual({ model: "grok-voice-latest", voice: "eve" });
    expect(restorePick(JSON.stringify({ model: "retired", voice: "x" }), REGISTRY)).toEqual(defaultPick(REGISTRY));
    expect(restorePick("{not json", REGISTRY)).toEqual(defaultPick(REGISTRY));
    expect(restorePick(JSON.stringify({ model: "deep" }), REGISTRY)).toEqual(defaultPick(REGISTRY));
    expect(restorePick(null, REGISTRY)).toEqual(defaultPick(REGISTRY));
    expect(restorePick(null, [])).toBeUndefined();
  });
  it("repairs a stale voice or effort within a model that still exists", () => {
    expect(restorePick(JSON.stringify({ model: "deep", voice: "gone", effort: "high" }), REGISTRY)).toEqual({ model: "deep", voice: "v1", effort: "high" });
    expect(restorePick(JSON.stringify({ model: "grok-voice-latest", voice: "sal", effort: "high" }), REGISTRY)).toEqual({ model: "grok-voice-latest", voice: "sal" });
  });
});
