import { useCallback, useEffect, useState } from "react";
import { type VoiceModel, type VoiceSelection, voiceSelectionSchema } from "../protocol";
import { effortsOf, pickModel } from "./reducer";

export const PICK_STORAGE_KEY = "osmo.voice-pick";

/** The registry's default pick: the first model with its default voice. */
export function defaultPick(registry: readonly VoiceModel[]): VoiceSelection | undefined {
  const first = registry[0];
  return first ? pickModel(first, { model: first.id, voice: first.defaultVoice }) : undefined;
}

/**
 * A saved pick, re-validated against the current registry. A missing model
 * falls back to the registry default; a missing voice to the model's default
 * voice; an effort the model no longer offers to its usual level.
 */
export function restorePick(saved: string | null, registry: readonly VoiceModel[]): VoiceSelection | undefined {
  const fallback = defaultPick(registry);
  if (saved === null) return fallback;
  let raw: unknown;
  try {
    raw = JSON.parse(saved);
  } catch {
    return fallback;
  }
  const parsed = voiceSelectionSchema.safeParse(raw);
  if (!parsed.success) return fallback;
  const model = registry.find((m) => m.id === parsed.data.model);
  if (!model) return fallback;
  const base = pickModel(model, parsed.data);
  const voice = model.voices.some((v) => v.id === parsed.data.voice) ? parsed.data.voice : model.defaultVoice;
  const effort = parsed.data.effort !== undefined && effortsOf(model).includes(parsed.data.effort) ? parsed.data.effort : base.effort;
  return effort === undefined ? { model: model.id, voice } : { model: model.id, voice, effort };
}

/** Picker value persisted in localStorage, restored (and re-validated) whenever the registry changes. */
export function usePersistedPick(
  registry: readonly VoiceModel[],
  key = PICK_STORAGE_KEY,
): [VoiceSelection | undefined, (next: VoiceSelection) => void] {
  const [pick, setPick] = useState<VoiceSelection | undefined>(() => restorePick(window.localStorage.getItem(key), registry));
  useEffect(() => {
    setPick(restorePick(window.localStorage.getItem(key), registry));
  }, [registry, key]);
  const save = useCallback(
    (next: VoiceSelection) => {
      window.localStorage.setItem(key, JSON.stringify(next));
      setPick(next);
    },
    [key],
  );
  return [pick, save];
}
