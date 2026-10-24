import type { Effort, VoiceModel, VoiceSelection } from "../protocol";
import { FREEZE_AFTER_SNAP_MS, RINGS, type RingKey, clampOffset, offsetFor, nearestSlot, slotAngle, snapOffset } from "./geometry";

export const EFFORTS: readonly Effort[] = ["low", "medium", "high"];

export interface ProviderGroup {
  id: string;
  models: VoiceModel[];
}

/** Registry grouped by `provider`, in first-seen order. */
export function groupProviders(registry: readonly VoiceModel[]): ProviderGroup[] {
  const groups = new Map<string, VoiceModel[]>();
  for (const model of registry) groups.set(model.provider, [...(groups.get(model.provider) ?? []), model]);
  return [...groups].map(([id, models]) => ({ id, models }));
}

/** Effort levels a model offers, in low → high order. */
export function effortsOf(model: VoiceModel | undefined): Effort[] {
  const allowed = model?.capabilities.reasoningEffort ?? [];
  return EFFORTS.filter((level) => allowed.includes(level));
}

/** The selection after committing `model`: its default voice, and the current effort if it still applies. */
export function pickModel(model: VoiceModel, current: VoiceSelection): VoiceSelection {
  const efforts = effortsOf(model);
  const effort = current.effort !== undefined && efforts.includes(current.effort) ? current.effort : efforts.includes("medium") ? "medium" : efforts[0];
  return effort === undefined ? { model: model.id, voice: model.defaultVoice } : { model: model.id, voice: model.defaultVoice, effort };
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

function openThird(state: PickerState, model: VoiceModel, value: VoiceSelection): PickerState {
  const voice = model.id === value.model ? model.voices.findIndex((v) => v.id === value.voice) : model.voices.findIndex((v) => v.id === model.defaultVoice);
  const efforts = effortsOf(model);
  const effort = model.id === value.model && value.effort !== undefined ? efforts.indexOf(value.effort) : -1;
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
  const providerAngle = slotAngle(RINGS.providers, providerIndex - state.offsets.providers);
  const selected = group.models.findIndex((model) => model.id === ctx.value.model);
  const anchor = Math.max(0, selected);
  const models = offsetFor(RINGS.models, group.models.length, anchor, nearestSlot(RINGS.models, providerAngle));
  return { ...state, depth: 2, provider: group.id, model: undefined, offsets: { ...state.offsets, models } };
}

export function reducePicker(state: PickerState, action: PickerAction): PickerState {
  switch (action.type) {
    case "open": {
      if (state.depth > 0) return state;
      const providers = offsetFor(RINGS.providers, action.groups.length, Math.max(0, selectedProvider(action)));
      return { ...initialPicker, depth: 1, offsets: { ...initialPicker.offsets, providers } };
    }
    case "close":
      return initialPicker;
    case "hoverProvider": {
      if (state.depth === 0 || state.provider === action.provider || isFrozen(state, "providers", action.now)) return state;
      const group = action.groups.find((g) => g.id === action.provider);
      return group ? openModels(state, group, action) : state;
    }
    case "hoverModel": {
      if (state.depth < 2 || state.model === action.model || isFrozen(state, "models", action.now)) return state;
      const model = action.groups.find((g) => g.id === state.provider)?.models.find((m) => m.id === action.model);
      return model ? openThird(state, model, action.value) : state;
    }
    case "scroll": {
      const ring = RINGS[action.ring];
      const offset = clampOffset(ring, action.count, state.offsets[action.ring] + action.slots);
      return { ...state, offsets: { ...state.offsets, [action.ring]: offset }, scroll: { ring: action.ring, settledAt: undefined } };
    }
    case "snap": {
      const offset = snapOffset(RINGS[action.ring], action.count, state.offsets[action.ring]);
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
      const ring = RINGS[action.ring];
      const current = state.offsets[action.ring];
      const lowest = action.index - (ring.visible - 1);
      const target = action.index < current ? action.index : action.index > current + ring.visible - 1 ? lowest : Math.round(current);
      return { ...state, offsets: { ...state.offsets, [action.ring]: clampOffset(ring, action.count, target) } };
    }
  }
}
