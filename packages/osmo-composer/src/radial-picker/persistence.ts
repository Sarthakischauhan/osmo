import { useCallback, useEffect, useState } from "react";
import { type VoiceModel, type VoiceSelection, voiceSelectionSchema } from "../protocol";
import { effortsOf, pickModel } from "./reducer";

/** v2: picks saved under "osmo.voice-pick" before c47dfbb hold the old first-listed default
 *  ("none" for xAI), saved on the first `voice.configured` as if chosen; they are not read. */
export const PICK_STORAGE_KEY = "osmo.voice-pick.v2";

/** The registry's default pick: the first model that speaks, with its default voice. */
export function defaultPick(registry: readonly VoiceModel[]): VoiceSelection | undefined {
  const first = registry.find((m) => m.capabilities.speaks !== false);
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
  // A transcription-only model cannot be the pick, so a saved one falls back like a missing one.
  const model = registry.find((m) => m.id === parsed.data.model && m.capabilities.speaks !== false);
  if (!model) return fallback;
  const base = pickModel(model, parsed.data);
  const voice = model.voices.some((v) => v.id === parsed.data.voice) ? parsed.data.voice : model.defaultVoice;
  const effort = parsed.data.effort !== undefined && effortsOf(model).includes(parsed.data.effort) ? parsed.data.effort : base.effort;
  return effort === undefined ? { model: model.id, voice } : { model: model.id, voice, effort };
}

/** The saved pick, or null when storage is unavailable (private mode, blocked, quota). */
export function readSavedPick(key = PICK_STORAGE_KEY): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSavedPick(key: string, next: VoiceSelection): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(next));
  } catch {
    // Storage unavailable: the pick still applies for this page.
  }
}

/**
 * Picker value restored from localStorage (re-validated whenever the registry changes).
 * `choose` only updates the in-memory pick; `save` also persists it, and is meant to be
 * called once the server confirms the pick with `voice.configured`.
 */
export function usePersistedPick(
  registry: readonly VoiceModel[],
  key = PICK_STORAGE_KEY,
): [VoiceSelection | undefined, (next: VoiceSelection) => void, (next: VoiceSelection) => void] {
  const [pick, setPick] = useState<VoiceSelection | undefined>(() => restorePick(readSavedPick(key), registry));
  useEffect(() => {
    setPick(restorePick(readSavedPick(key), registry));
  }, [registry, key]);
  const save = useCallback(
    (next: VoiceSelection) => {
      writeSavedPick(key, next);
      setPick(next);
    },
    [key],
  );
  return [pick, save, setPick];
}
