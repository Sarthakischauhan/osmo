import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { type VoiceModel, voiceModelSchema } from "../src/protocol";
import {
  BANDS,
  OPTION_TOKENS,
  optionTokenVars,
  FREEZE_AFTER_SNAP_MS,
  RINGS,
  clampOffset,
  edgeFalloff,
  hitTest,
  itemAt,
  itemPresence,
  moreMarkers,
  nearestSlot,
  offsetFor,
  polar,
  slotAngle,
  ringSpec,
  snapOffset,
} from "../src/radial-picker/geometry";
import { PICK_STORAGE_KEY, defaultPick, readSavedPick, restoreMemory, restorePick } from "../src/radial-picker/persistence";
import { focusVisible, modelRingLabel } from "../src/radial-picker/RadialPicker";
import { modelLabel } from "../src/radial-picker/ringLabels";
import {
  type LogoFetch,
  isPlaceholderLogo,
  providerIcons,
  resetLogoChecks,
  resolveLogo,
} from "../src/radial-picker/providerIcons";
import {
  effortsOf,
  emptyMemory,
  groupProviders,
  initialPicker,
  isFrozen,
  pickModel,
  recallModel,
  recallProvider,
  reducePicker,
  remember,
} from "../src/radial-picker/reducer";

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
    expect([0, 1, 2, 3].map((p) => slotAngle(RINGS.providers, p))).toEqual([150, 110, 70, 30]);
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
    expect(clampOffset(RINGS.models, 3, -5)).toBe(-2);
    expect(clampOffset(RINGS.models, 3, 1)).toBe(0);
  });
  it("spreads fewer providers than slots evenly around 90° with real gaps", () => {
    const three = ringSpec("providers", 3);
    expect([0, 1, 2].map((p) => slotAngle(three, p))).toEqual([130, 90, 50]);
    expect(clampOffset(three, 3, -1)).toBe(0);
    expect(slotAngle(ringSpec("providers", 1), 0)).toBe(90);
    expect(ringSpec("providers", 7)).toBe(RINGS.providers);
    // Neighbouring 44px discs don't touch: chord between slots exceeds the disc size.
    const chord = 2 * RINGS.providers.radius * Math.sin((RINGS.providers.step * Math.PI) / 360);
    expect(chord - 44).toBeGreaterThan(10);
  });
  it("snaps to the nearest whole slot, clamped", () => {
    expect(snapOffset(RINGS.models, 9, 2.4)).toBe(2);
    expect(snapOffset(RINGS.models, 9, 2.6)).toBe(3);
    expect(snapOffset(RINGS.models, 9, 4.4)).toBe(4);
  });
  it("centres the window on an anchor", () => {
    expect(offsetFor(RINGS.models, 9, 6)).toBe(4);
    expect(offsetFor(RINGS.models, 9, 0)).toBe(0);
    expect(offsetFor(RINGS.models, 3, 0)).toBe(-2);
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
    expect(ring(BANDS.r1 - 1, 90)).toBe("providers");
    expect(ring(BANDS.r1, 90)).toBe("models");
    expect(ring(BANDS.r2 - 1, 90)).toBe("models");
    expect(ring(BANDS.r2, 120)).toBe("voices");
    expect(ring(BANDS.r3 - 1, 60)).toBe("effort");
    expect(ring(BANDS.r3, 60)).toBe("none");
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
  it("a provider whose models are all locked is a locked group whose models ring never opens", () => {
    const locked = groupProviders(REGISTRY.map((m) => (m.provider === "openai" ? { ...m, locked: true } : m)));
    expect(locked.find((g) => g.id === "openai")?.locked).toBe(true);
    expect(locked.filter((g) => g.id !== "openai").every((g) => !g.locked)).toBe(true);
    const opened = reducePicker(initialPicker, { type: "open", groups: locked, value });
    expect(reducePicker(opened, { type: "hoverProvider", provider: "openai", now: 0, groups: locked, value }).depth).toBe(1);
    const other = locked.find((g) => g.id !== "openai")?.id ?? "";
    const r2 = reducePicker(opened, { type: "hoverProvider", provider: other, now: 0, groups: locked, value });
    expect(reducePicker(r2, { type: "hoverProvider", provider: "openai", now: 0, groups: locked, value })).toMatchObject({ depth: 1, provider: undefined });
  });
  it("shows every known provider: those without voice models are locked with a reason", () => {
    const xai = [model("xai", "grok-voice-latest", ["eve"], ["none", "high"])];
    const known = [
      { id: "xai", label: "xAI" },
      { id: "openai", label: "OpenAI" },
      { id: "vercel", label: "Vercel AI Gateway" },
      { id: "openrouter", label: "OpenRouter" },
    ];
    const ring = groupProviders(xai, known);
    expect(ring.map((g) => [g.id, g.locked, g.reason ?? null])).toEqual([
      ["xai", false, null],
      ["openai", true, "No voice models yet"],
      ["vercel", true, "No voice models yet"],
      ["openrouter", true, "No voice models yet"],
    ]);
    expect(ring.find((g) => g.id === "vercel")?.label).toBe("Vercel AI Gateway");
    const unconnected = groupProviders(xai.map((m) => ({ ...m, locked: true })), known);
    expect(unconnected[0]).toMatchObject({ id: "xai", locked: true, reason: "not connected" });
    const opened = reducePicker(initialPicker, { type: "open", groups: ring, value });
    expect(reducePicker(opened, { type: "hoverProvider", provider: "openai", now: 0, groups: ring, value }).depth).toBe(1);
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
  it("without medium, a new model starts at its highest effort, however the registry lists them", () => {
    const xai = model("xai", "grok-voice-latest", ["eve"], ["none", "high"]);
    expect(pickModel(xai, { model: "x", voice: "v" })).toEqual({ model: "grok-voice-latest", voice: "eve", effort: "high" });
  });
  it("orders efforts low → high and hides them when absent", () => {
    expect(effortsOf(REGISTRY.find((m) => m.id === "m4"))).toEqual(["low", "high"]);
    expect(effortsOf(REGISTRY[0])).toEqual([]);
  });
});

describe("remembered paths", () => {
  const xai = [model("xai", "grok-voice-latest", ["eve", "ara"], ["none", "high"]), model("xai", "grok-voice-3.0", ["eve", "ara", "sal"], ["none", "high"])];
  const registry = [...xai, ...REGISTRY.slice(1)];
  const providers = groupProviders(registry);
  const xaiGroup = providers.find((g) => g.id === "xai");
  const openaiGroup = providers.find((g) => g.id === "openai");
  if (!xaiGroup || !openaiGroup) throw new Error("fixture");
  const chosen = { model: "grok-voice-3.0", voice: "sal", effort: "none" as const };
  const memory = remember(emptyMemory, registry, chosen);
  const elsewhere = { model: "m1", voice: "b" };

  it("a provider commit recalls its last model with that model's voice and effort", () => {
    expect(recallProvider(xaiGroup, memory, elsewhere)).toEqual(chosen);
    expect(recallProvider(xaiGroup, emptyMemory, elsewhere)).toEqual({ model: "grok-voice-latest", voice: "eve", effort: "high" });
  });
  it("a model commit recalls its own voice and effort, not its defaults", () => {
    const three = xai[1];
    if (!three) throw new Error("fixture");
    expect(recallModel(three, memory, elsewhere)).toEqual(chosen);
    expect(recallModel(three, emptyMemory, elsewhere)).toEqual({ model: "grok-voice-3.0", voice: "eve", effort: "high" });
  });
  it("the current pick wins over memory for its own provider and model", () => {
    const current = { model: "grok-voice-latest", voice: "ara", effort: "none" as const };
    expect(recallProvider(xaiGroup, memory, current)).toEqual(current);
  });
  it("opening a remembered model's ring centres its remembered voice", () => {
    const opened = reducePicker(initialPicker, { type: "open", groups: providers, value: elsewhere, memory });
    const r2 = reducePicker(opened, { type: "hoverProvider", provider: "xai", now: 0, groups: providers, value: elsewhere, memory });
    const r3 = reducePicker(r2, { type: "hoverModel", model: "grok-voice-3.0", now: 0, groups: providers, value: elsewhere, memory });
    expect(r3.offsets.voices).toBe(offsetFor(RINGS.voices, 3, 2));
  });
  it("memory survives storage as JSON and junk reads as empty", () => {
    expect(restoreMemory(JSON.stringify(memory))).toEqual(memory);
    expect(restoreMemory("{nope")).toEqual(emptyMemory);
    expect(restoreMemory(null)).toEqual(emptyMemory);
  });
  it("forgets nothing about other providers", () => {
    const both = remember(memory, registry, { model: "m4", voice: "b", effort: "low" });
    expect(recallProvider(xaiGroup, both, elsewhere)).toEqual(chosen);
    expect(recallProvider(openaiGroup, both, { model: "deep", voice: "v1" })).toEqual({ model: "m4", voice: "b", effort: "low" });
  });
});

describe("persistence", () => {
  it("uses the key osmo.voice-pick.v2, so picks saved under the old 'none' default are not read", () =>
    expect(PICK_STORAGE_KEY).toBe("osmo.voice-pick.v2"));
  it("defaults xAI's realtime models to high effort, as the server's default_selection does", () => {
    const xai = [model("xai", "grok-voice-latest", ["eve"], ["none", "high"]), model("xai", "grok-voice-3.0", ["eve"], ["none", "high"])];
    expect(defaultPick(xai)).toEqual({ model: "grok-voice-latest", voice: "eve", effort: "high" });
    expect(restorePick(null, xai)?.effort).toBe("high");
  });
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

describe("transcription-only models (Sarvam realtime STT)", () => {
  const saaras: VoiceModel = {
    ...model("sarvam", "saaras:v4", []),
    capabilities: { tools: false, interrupt: false, customVoices: false, reasoningEffort: [], speaks: false },
  };
  const withSarvam = [saaras, ...REGISTRY];
  it("parses speaks from the wire and keeps it optional", () => {
    expect(voiceModelSchema.parse(saaras).capabilities.speaks).toBe(false);
    expect(voiceModelSchema.parse(REGISTRY[0]).capabilities.speaks).toBeUndefined();
  });
  it("groups Sarvam as an unlocked provider with its models listed", () => {
    const sarvam = groupProviders(withSarvam).find((g) => g.id === "sarvam");
    expect(sarvam?.locked).toBe(false);
    expect(sarvam?.models.map((m) => m.id)).toEqual(["saaras:v4"]);
  });
  it("labels the model ring entry transcription only", () => {
    expect(modelLabel(saaras).tip).toBe("saaras:v4 · transcription only");
    expect(modelLabel(REGISTRY[0]).tip).toBe("grok-voice-latest");
  });
  it("never defaults to or restores a transcription-only model", () => {
    expect(defaultPick(withSarvam)).toEqual({ model: "grok-voice-latest", voice: "eve" });
    expect(restorePick(JSON.stringify({ model: "saaras:v4", voice: "x" }), withSarvam)).toEqual(defaultPick(withSarvam));
  });
  it("uses Sarvam's official logomark at runtime and bundles none", () => {
    expect(providerIcons.sarvam).toEqual({ label: "Sarvam AI", logo: "https://assets.sarvam.ai/assets/brand/logos/sarvam-logo-black.svg" });
  });
});

describe("provider logo fallback chain", () => {
  const real = '<svg><path d="M12.4579 15.6036" fill="currentColor"/></svg>';
  const placeholder = '<svg><path d="M9.8132 15.9038L9 18.75" stroke="currentColor"/></svg>';
  const serve =
    (pages: Record<string, { ok: boolean; body: string } | "network-error">): LogoFetch =>
    async (url) => {
      const page = pages[url];
      if (page === undefined || page === "network-error") throw new Error("offline");
      return { ok: page.ok, text: async () => page.body };
    };
  const entry = { label: "Acme", logo: "https://models.dev/logos/acme.svg", fallback: "/assets/acme.svg" };

  it("uses the runtime models.dev logo first", async () => {
    resetLogoChecks();
    expect(await resolveLogo(entry, new Set(), serve({ [entry.logo]: { ok: true, body: real } }))).toBe(entry.logo);
  });

  it("falls back to the bundled logo on a placeholder, an HTTP error or a network error", async () => {
    for (const page of [{ ok: true, body: placeholder }, { ok: false, body: "" }, "network-error" as const]) {
      resetLogoChecks();
      expect(await resolveLogo(entry, new Set(), serve({ [entry.logo]: page }))).toBe(entry.fallback);
    }
  });

  it("falls back to the bundled logo when the runtime <img> fails to load", async () => {
    resetLogoChecks();
    const fetcher = serve({ [entry.logo]: { ok: true, body: real } });
    expect(await resolveLogo(entry, new Set([entry.logo]), fetcher)).toBe(entry.fallback);
  });

  it("gives a monogram (undefined) when neither is usable", async () => {
    resetLogoChecks();
    const fetcher = serve({ [entry.logo]: { ok: true, body: placeholder } });
    expect(await resolveLogo({ label: "Acme", logo: entry.logo }, new Set(), fetcher)).toBeUndefined();
    expect(await resolveLogo(entry, new Set([entry.fallback]), fetcher)).toBeUndefined();
    expect(await resolveLogo({ label: "Acme" })).toBeUndefined();
  });

  it("detects the models.dev placeholder", () => {
    expect(isPlaceholderLogo(placeholder)).toBe(true);
    expect(isPlaceholderLogo(real)).toBe(false);
  });

  it("bundles fallbacks only for providers models.dev really has", () => {
    const bundledFor = Object.entries(providerIcons).filter(([, icon]) => icon.fallback !== undefined).map(([id]) => id);
    expect(bundledFor).toEqual(["xai", "openai", "google", "anthropic", "mistral"]);
    // Every runtime logo is models.dev's, except Sarvam's official logomark (models.dev has none).
    for (const [id, icon] of Object.entries(providerIcons)) {
      if (id !== "sarvam") expect(icon.logo).toMatch(/^https:\/\/models\.dev\/logos\/.+\.svg$/);
    }
  });
});

describe("persistence storage errors", () => {
  it("reads null when localStorage throws", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "window");
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        get localStorage(): Storage {
          throw new Error("SecurityError");
        },
      },
    });
    try {
      expect(readSavedPick()).toBeNull();
    } finally {
      if (original) Object.defineProperty(globalThis, "window", original);
      else Reflect.deleteProperty(globalThis, "window");
    }
  });
});

describe("focusVisible", () => {
  it("is true only when the element matches :focus-visible, false when unsupported", () => {
    const element = (matches: (selector: string) => boolean) => ({ matches }) as unknown as Element;
    expect(focusVisible(element((s) => s === ":focus-visible"))).toBe(true);
    expect(focusVisible(element(() => false))).toBe(false);
    expect(
      focusVisible(
        element(() => {
          throw new SyntaxError("unsupported");
        }),
      ),
    ).toBe(false);
  });
});

describe("model ring labels", () => {
  it("shows the short label, else the label", () => {
    expect(modelRingLabel({ label: "Grok Voice", shortLabel: "Latest" })).toBe("Latest");
    expect(modelRingLabel({ label: "Grok Voice 3.0", shortLabel: "3.0" })).toBe("3.0");
    expect(modelRingLabel({ label: "Thinker" })).toBe("Thinker");
    expect(modelRingLabel({ label: "Thinker", shortLabel: null })).toBe("Thinker");
  });
  it("accepts shortLabel on the wire, optional", () => {
    const base = model("xai", "grok-voice-3.0", ["eve"]);
    expect(voiceModelSchema.parse({ ...base, shortLabel: "3.0" }).shortLabel).toBe("3.0");
    expect(voiceModelSchema.parse(base).shortLabel).toBeUndefined();
  });
  it("never truncates the model ring text and styles the id tooltip without a border", () => {
    const css = readFileSync(new URL("../src/radial-picker/radialPicker.css", import.meta.url), "utf8");
    const rule = (sel: string) => css.match(new RegExp(`\\${sel} \\{([^}]*)\\}`))?.[1] ?? "";
    expect(rule(".rp-option")).not.toMatch(/ellipsis|max-width|overflow/);
    expect(rule(".rp-tip")).toMatch(/border: 0/);
    expect(rule(".rp-tip")).toMatch(/background: var\(--accent\)/);
    expect(css).not.toMatch(/backdrop-filter|radial-gradient/);
    expect(css).not.toMatch(/box-shadow: (?!none)/);
  });
});



describe("a 28-voice ring (xAI's documented roster)", () => {
  it("shows a window with … markers, fades mid-glide and settles on whole slots", () => {
    expect(moreMarkers(RINGS.voices, 28, 0)).toEqual({ before: false, after: true });
    expect(moreMarkers(RINGS.voices, 28, 12)).toEqual({ before: true, after: true });
    expect(moreMarkers(RINGS.voices, 28, 25)).toEqual({ before: true, after: false });
    expect(snapOffset(RINGS.voices, 28, 12.4)).toBe(12);
    expect(snapOffset(RINGS.voices, 28, 40)).toBe(25);
    const presence = itemPresence(RINGS.voices, -0.25);
    expect(presence.opacity).toBeGreaterThan(0);
    expect(presence.opacity).toBeLessThan(1);
  });
});

describe("one option primitive", () => {
  const css = readFileSync(new URL("../src/radial-picker/radialPicker.css", import.meta.url), "utf8");
  it("has no per-ring option rules", () => {
    expect(css).not.toMatch(/\.rp-(providers|models|voices|effort)\b/);
    expect(css).not.toMatch(/\[data-ring/);
    expect(css).not.toMatch(/\.rp-item\b/);
  });
  it("sizes .rp-option only from the shared tokens", () => {
    const option = css.match(/\.rp-option \{([^}]*)\}/)?.[1] ?? "";
    expect(option).toMatch(/height: var\(--rp-option-height\)/);
    expect(option).toMatch(/padding: 0 var\(--rp-option-padding-x\)/);
    expect(option).toMatch(/border-radius: var\(--rp-option-radius\)/);
    expect(option).toMatch(/gap: var\(--rp-option-gap\)/);
    expect(option).toMatch(/border: var\(--picker-border\)/);
  });
  it("declares the same token values in CSS as OPTION_TOKENS", () => {
    for (const [name, value] of Object.entries(optionTokenVars())) {
      expect(css).toContain(`${name}: ${value};`);
    }
  });
  it("keeps hit areas at least 44px and spaces rings by --rp-ring-spacing", () => {
    expect(OPTION_TOKENS.hit).toBeGreaterThanOrEqual(44);
    expect(OPTION_TOKENS.height).toBeLessThanOrEqual(OPTION_TOKENS.hit);
    expect(RINGS.models.radius - RINGS.providers.radius).toBe(OPTION_TOKENS.ringSpacing);
    expect(RINGS.voices.radius - RINGS.models.radius).toBe(OPTION_TOKENS.ringSpacing);
    expect(RINGS.effort.radius).toBe(RINGS.voices.radius);
  });
});

