import type { Effort, VoiceModel } from "@osmo/osmo-composer";

const caps = (reasoningEffort?: Effort[]): VoiceModel["capabilities"] =>
  reasoningEffort ? { tools: true, interrupt: true, customVoices: false, reasoningEffort } : { tools: true, interrupt: true, customVoices: false };

const model = (provider: string, id: string, label: string, voices: [string, string, string[]][], effort?: Effort[]): VoiceModel => ({
  id,
  provider,
  label,
  capabilities: caps(effort),
  voices: voices.map(([voiceId, voiceLabel, traits]) => ({ id: voiceId, label: voiceLabel, traits })),
  defaultVoice: voices[0]?.[0] ?? "",
});

const duo: [string, string, string[]][] = [
  ["alloy", "Alloy", ["neutral", "clear"]],
  ["onyx", "Onyx", ["deep", "steady"]],
];

/**
 * Test fixture (never bundled): 7 providers, one (openai) with 9 models, one model
 * (claude-voice-1) with 6 voices and all three efforts, plus the real
 * grok-voice-latest with Eve, Ara and Sal. Used by the main-app picker and provider-auth e2e.
 */
export const MOCK_REGISTRY: VoiceModel[] = [
  model("xai", "grok-voice-latest", "Grok Voice", [
    ["eve", "Eve", ["warm", "bright", "upbeat"]],
    ["ara", "Ara", ["calm", "soft", "friendly"]],
    ["sal", "Sal", ["deep", "smooth", "steady"]],
  ]),
  model("openai", "gpt-realtime", "Realtime", duo, ["low", "medium", "high"]),
  model("openai", "gpt-realtime-mini", "RT mini", duo),
  model("openai", "gpt-4o-realtime", "4o RT", duo),
  model("openai", "gpt-4o-mini-realtime", "4o mini RT", duo),
  model("openai", "gpt-realtime-2025-08", "RT 08-25", duo, ["low", "high"]),
  model("openai", "gpt-realtime-2025-06", "RT 06-25", duo),
  model("openai", "gpt-4o-audio", "4o audio", duo),
  model("openai", "gpt-4o-mini-audio", "4o mini aud", duo),
  model("openai", "gpt-audio-preview", "Audio prev", duo),
  model("google", "gemini-live-2.5-flash", "Live Flash", [["puck", "Puck", ["bright"]], ["kore", "Kore", ["firm"]]], ["low", "medium"]),
  model("anthropic", "claude-voice-1", "Claude V1", [
    ["ember", "Ember", ["warm", "low"]],
    ["harbor", "Harbor", ["deep", "calm"]],
    ["quill", "Quill", ["crisp", "light"]],
    ["sable", "Sable", ["deep", "rich"]],
    ["wren", "Wren", ["bright", "quick"]],
    ["moss", "Moss", ["soft", "slow"]],
  ], ["low", "medium", "high"]),
  model("elevenlabs", "eleven-flash-v2.5", "Flash 2.5", [["rachel", "Rachel", ["calm"]], ["adam", "Adam", ["deep"]]]),
  model("deepgram", "aura-2", "Aura 2", [["thalia", "Thalia", ["clear"]], ["orion", "Orion", ["deep"]]]),
  model("cartesia", "sonic-2", "Sonic 2", [["sky", "Sky", ["airy"]], ["stone", "Stone", ["low"]]]),
];

/** Providers the mock server reports as not connected; their models arrive `locked`. */
export const LOCKED_PROVIDERS = ["openai", "openrouter"];

/** MOCK_REGISTRY plus an OpenRouter model, with LOCKED_PROVIDERS' models locked (provider-auth e2e). */
export const LOCKED_REGISTRY: VoiceModel[] = [...MOCK_REGISTRY, model("openrouter", "openrouter-voice", "OR Voice", duo)].map((m) =>
  LOCKED_PROVIDERS.includes(m.provider) ? { ...m, locked: true } : m,
);
