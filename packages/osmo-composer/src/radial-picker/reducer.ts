import type { Effort, VoiceModel, VoiceSelection } from "../protocol";
import { FREEZE_AFTER_SNAP_MS, RINGS, type RingKey, ringSpec, clampOffset, offsetFor, nearestSlot, slotAngle, snapOffset } from "./geometry";

export const EFFORTS: readonly Effort[] = ["none", "minimal", "low", "medium", "high", "xhigh"];

/** A provider the server knows (`GET /providers`), whether or not it serves voice models. */
export interface PickerProvider {
  id: string;
  label: string;
}

/** Why a provider chip is locked. */
export type LockReason = "not connected" | "No voice models yet";

export interface ProviderGroup {
  id: string;
  /** The server's label, for providers the icon table does not know. */
  label?: string;
  models: VoiceModel[];
  /** Nothing to pick: every model is locked (not connected), or the provider has no voice models. */
  locked: boolean;
  reason?: LockReason;
}

/** Registry grouped by `provider`, in first-seen order, then every other known provider as a
 *  locked chip: only some providers have realtime voice models, and the rest stay visible. */
export function groupProviders(registry: readonly VoiceModel[], providers: readonly PickerProvider[] = []): ProviderGroup[] {
  const byId = new Map<string, VoiceModel[]>();
  for (const model of registry) byId.set(model.provider, [...(byId.get(model.provider) ?? []), model]);
  const label = (id: string) => providers.find((p) => p.id === id)?.label;
  const withModels = [...byId].map(([id, models]): ProviderGroup => {
    const locked = models.every((model) => model.locked === true);
    return { id, label: label(id), models, locked, reason: locked ? "not connected" : undefined };
  });
  const withoutModels = providers
    .filter((p) => !byId.has(p.id))
    .map((p): ProviderGroup => ({ id: p.id, label: p.label, models: [], locked: true, reason: "No voice models yet" }));
  return [...withModels, ...withoutModels];
}

/** Effort levels a model offers, in low → high order. */
export function effortsOf(model: VoiceModel | undefined): Effort[] {
  const allowed = model?.capabilities.reasoningEffort ?? [];
  return EFFORTS.filter((level) => allowed.includes(level));
}

/** The selection after committing `model`: its default voice, and the current effort if it still applies,
 *  else "medium" or the highest level it offers (the server's default_effort rule). */
export function pickModel(model: VoiceModel, current: VoiceSelection): VoiceSelection {
  const efforts = effortsOf(model);
  const effort = current.effort !== undefined && efforts.includes(current.effort) ? current.effort : efforts.includes("medium") ? "medium" : efforts.at(-1);
  return effort === undefined ? { model: model.id, voice: model.defaultVoice } : { model: model.id, voice: model.defaultVoice, effort };
}

/** The last pick made under each provider and on each model, so committing either one recalls
 *  the rest of its path (xAI → Grok Voice 3.0 → Eve) instead of falling back to defaults. */
export interface PickMemory {
  /** Provider id → the model last picked from it. */
  providers: Record<string, string>;
  /** Model id → the voice and effort last picked on it. */
  models: Record<string, { voice: string; effort?: Effort }>;
}

export const emptyMemory: PickMemory = { providers: {}, models: {} };

export function remember(memory: PickMemory, registry: readonly VoiceModel[], pick: VoiceSelection): PickMemory {
  const model = registry.find((m) => m.id === pick.model);
  if (!model) return memory;
  const last = pick.effort === undefined ? { voice: pick.voice } : { voice: pick.voice, effort: pick.effort };
  return { providers: { ...memory.providers, [model.provider]: model.id }, models: { ...memory.models, [model.id]: last } };
}

/** `model` with `wanted`'s voice and effort where it offers them, else its defaults. */
export function onModel(model: VoiceModel, wanted: { voice?: string; effort?: Effort }): VoiceSelection {
  const current = wanted.effort === undefined ? { model: model.id, voice: model.defaultVoice } : { model: model.id, voice: model.defaultVoice, effort: wanted.effort };
  const voice = wanted.voice !== undefined && model.voices.some((v) => v.id === wanted.voice) ? wanted.voice : model.defaultVoice;
  return { ...pickModel(model, current), voice };
}

/** The full pick for `model`: the current one if it is that model, else the voice and effort last
 *  picked on it, else its default voice and the current effort if it offers it. */
export function recallModel(model: VoiceModel, memory: PickMemory, current: VoiceSelection): VoiceSelection {
  if (model.id === current.model) return onModel(model, current);
  return onModel(model, memory.models[model.id] ?? { effort: current.effort });
}

/** The full pick for a provider: its current or last-picked model (else its first usable one), recalled. */
export function recallProvider(group: ProviderGroup, memory: PickMemory, current: VoiceSelection): VoiceSelection | undefined {
  const usable = group.models.filter((model) => model.locked !== true);
  const model =
    usable.find((m) => m.id === current.model) ?? usable.find((m) => m.id === memory.providers[group.id]) ?? usable[0];
  return model && recallModel(model, memory, current);
}

export type Depth = 0 | 1 | 2 | 3;

export interface PickerState {
  /** How many rings are open: 0 closed, 1 providers, 2 + models, 3 + voices/effort. */
  depth: Depth;
  /** The open path: the provider whose models show, the model whose voices/effort show. */
  provider: string | undefined;
  model: string | undefined;
  /** Fractional slot offset per ring. */
  offsets: Record<RingKey, number>;
  /** The ring being scrolled; outer rings stay frozen until FREEZE_AFTER_SNAP_MS after it settles. */
  scroll: { ring: RingKey; settledAt: number | undefined } | undefined;
}

export const initialPicker: PickerState = {
  depth: 0,
  provider: undefined,
  model: undefined,
  offsets: { providers: 0, models: 0, voices: 0, effort: 0 },
  scroll: undefined,
};

const ORDER: Record<RingKey, number> = { providers: 1, models: 2, voices: 3, effort: 3 };

/** True if hovering an item of `ring` must not open or switch the rings outside it right now. */
export function isFrozen(state: PickerState, ring: RingKey, now: number): boolean {
  const scroll = state.scroll;
  if (!scroll || ORDER[scroll.ring] > ORDER[ring]) return false;
  return scroll.settledAt === undefined || now < scroll.settledAt + FREEZE_AFTER_SNAP_MS;
}

export interface Context {
  groups: ProviderGroup[];
  value: VoiceSelection;
  memory?: PickMemory;
}

export type PickerAction =
  | ({ type: "open" } & Context)
  | { type: "close" }
  | ({ type: "hoverProvider"; provider: string; now: number } & Context)
  | ({ type: "hoverModel"; model: string; now: number } & Context)
  | { type: "scroll"; ring: RingKey; slots: number; count: number }
  | { type: "snap"; ring: RingKey; count: number }
  | { type: "settle"; ring: RingKey; now: number }
  | { type: "thaw"; now: number }
  | { type: "reveal"; ring: RingKey; index: number; count: number };

function selectedProvider({ groups, value }: Context): number {
  return groups.findIndex((group) => group.models.some((model) => model.id === value.model));
}

function openThird(state: PickerState, model: VoiceModel, ctx: Context): PickerState {
  const recalled = recallModel(model, ctx.memory ?? emptyMemory, ctx.value);
  const voice = model.voices.findIndex((v) => v.id === recalled.voice);
  const efforts = effortsOf(model);
  const effort = recalled.effort === undefined ? -1 : efforts.indexOf(recalled.effort);
  return {
    ...state,
    depth: 3,
    model: model.id,
    offsets: {
      ...state.offsets,
      voices: offsetFor(RINGS.voices, model.voices.length, Math.max(0, voice)),
      effort: offsetFor(RINGS.effort, efforts.length, Math.max(0, effort)),
    },
  };
}

function openModels(state: PickerState, group: ProviderGroup, ctx: Context): PickerState {
  const providerIndex = ctx.groups.findIndex((g) => g.id === group.id);
  const providerAngle = slotAngle(ringSpec("providers", ctx.groups.length), providerIndex - state.offsets.providers);
  const recalled = recallProvider(group, ctx.memory ?? emptyMemory, ctx.value);
  const anchor = Math.max(0, group.models.findIndex((model) => model.id === recalled?.model));
  const models = offsetFor(RINGS.models, group.models.length, anchor, nearestSlot(RINGS.models, providerAngle));
  return { ...state, depth: 2, provider: group.id, model: undefined, offsets: { ...state.offsets, models } };
}

export function reducePicker(state: PickerState, action: PickerAction): PickerState {
  switch (action.type) {
    case "open": {
      if (state.depth > 0) return state;
      const providers = offsetFor(ringSpec("providers", action.groups.length), action.groups.length, Math.max(0, selectedProvider(action)));
      return { ...initialPicker, depth: 1, offsets: { ...initialPicker.offsets, providers } };
    }
    case "close":
      return initialPicker;
    case "hoverProvider": {
      if (state.depth === 0 || state.provider === action.provider || isFrozen(state, "providers", action.now)) return state;
      // A locked provider has nothing to pick yet: its models ring stays closed.
      const group = action.groups.find((g) => g.id === action.provider);
      if (group?.locked) return state.depth > 1 ? { ...state, depth: 1, provider: undefined, model: undefined } : state;
      return group ? openModels(state, group, action) : state;
    }
    case "hoverModel": {
      if (state.depth < 2 || state.model === action.model || isFrozen(state, "models", action.now)) return state;
      const model = action.groups.find((g) => g.id === state.provider)?.models.find((m) => m.id === action.model);
      return model ? openThird(state, model, action) : state;
    }
    case "scroll": {
      const ring = ringSpec(action.ring, action.count);
      const offset = clampOffset(ring, action.count, state.offsets[action.ring] + action.slots);
      return { ...state, offsets: { ...state.offsets, [action.ring]: offset }, scroll: { ring: action.ring, settledAt: undefined } };
    }
    case "snap": {
      const offset = snapOffset(ringSpec(action.ring, action.count), action.count, state.offsets[action.ring]);
      return { ...state, offsets: { ...state.offsets, [action.ring]: offset } };
    }
    case "settle":
      return state.scroll?.ring === action.ring ? { ...state, scroll: { ring: action.ring, settledAt: action.now } } : state;
    case "thaw": {
      const scroll = state.scroll;
      if (!scroll || scroll.settledAt === undefined || action.now < scroll.settledAt + FREEZE_AFTER_SNAP_MS) return state;
      return { ...state, scroll: undefined };
    }
    case "reveal": {
      const ring = ringSpec(action.ring, action.count);
      const current = state.offsets[action.ring];
      const lowest = action.index - (ring.visible - 1);
      const target = action.index < current ? action.index : action.index > current + ring.visible - 1 ? lowest : Math.round(current);
      return { ...state, offsets: { ...state.offsets, [action.ring]: clampOffset(ring, action.count, target) } };
    }
  }
}
