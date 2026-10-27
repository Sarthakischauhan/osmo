import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { type VoiceModel, type VoiceSelection, effortSchema, voiceSelectionSchema } from "../protocol";
import { type PickMemory, emptyMemory, onModel, pickModel, remember } from "./reducer";

/** v2: picks saved under "osmo.voice-pick" before c47dfbb hold the old first-listed default
 *  ("none" for xAI), saved on the first `voice.configured` as if chosen; they are not read. */
export const PICK_STORAGE_KEY = "osmo.voice-pick.v2";
/** The per-provider and per-model memory (`PickMemory`) kept next to the pick. */
export const MEMORY_STORAGE_KEY = "osmo.voice-memory.v1";

const memorySchema = z.object({
  providers: z.record(z.string()),
  models: z.record(z.object({ voice: z.string(), effort: effortSchema.optional() })),
});

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
  return model ? onModel(model, parsed.data) : fallback;
}

/** Saved memory; empty when missing or unreadable. Entries are re-validated when recalled. */
export function restoreMemory(saved: string | null): PickMemory {
  if (saved === null) return emptyMemory;
  try {
    const parsed = memorySchema.safeParse(JSON.parse(saved));
    return parsed.success ? parsed.data : emptyMemory;
  } catch {
    return emptyMemory;
  }
}

/** The saved pick, or null when storage is unavailable (private mode, blocked, quota). */
export function readSavedPick(key = PICK_STORAGE_KEY): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable: the pick still applies for this page.
  }
}

/**
 * Picker value and memory restored from localStorage (the pick re-validated whenever the
 * registry changes). `save` applies a pick and persists it with the memory, at once: idle there is
 * no server to confirm it, and an unsaved pick would be reverted by the next registry refresh.
 */
export function usePersistedPick(
  registry: readonly VoiceModel[],
  key = PICK_STORAGE_KEY,
  memoryKey = MEMORY_STORAGE_KEY,
): [VoiceSelection | undefined, (next: VoiceSelection) => void, PickMemory] {
  const [pick, setPick] = useState<VoiceSelection | undefined>(() => restorePick(readSavedPick(key), registry));
  const [memory, setMemory] = useState<PickMemory>(() => restoreMemory(readSavedPick(memoryKey)));
  const registryRef = useRef(registry);
  registryRef.current = registry;
  useEffect(() => {
    setPick(restorePick(readSavedPick(key), registry));
  }, [registry, key]);
  const save = useCallback(
    (next: VoiceSelection) => {
      write(key, next);
      setPick(next);
      setMemory((previous) => {
        const updated = remember(previous, registryRef.current, next);
        write(memoryKey, updated);
        return updated;
      });
    },
    [key, memoryKey],
  );
  return [pick, save, memory];
}
