import { z } from "zod";

/**
 * Osmo wire protocol: one WebSocket between the browser and Symphony.
 * Binary frames carry PCM16 audio (24 kHz mono, little-endian) both ways.
 * Text frames carry the JSON events below. Nothing here mirrors Grok's
 * event format, so the composer can sit on top of any backend.
 */

export const AUDIO_SAMPLE_RATE = 24000;

/** `state.detail` value that tells the client to flush queued assistant audio now. */
export const BARGE_IN = "barge-in";

export const osmoStateSchema = z.enum(["idle", "listening", "thinking", "speaking", "working"]);
export type OsmoState = z.infer<typeof osmoStateSchema>;

export const effortSchema = z.enum(["low", "medium", "high"]);
export type Effort = z.infer<typeof effortSchema>;

/** One voice model in Symphony's registry (the `models` event); mirrors symphony_server/voices.py. */
export const voiceModelSchema = z.object({
  id: z.string(),
  provider: z.string(),
  label: z.string(),
  capabilities: z.object({
    tools: z.boolean(),
    interrupt: z.boolean(),
    customVoices: z.boolean(),
    reasoningEffort: z.array(effortSchema).optional(),
  }),
  voices: z.array(z.object({ id: z.string(), label: z.string(), traits: z.array(z.string()) })),
  defaultVoice: z.string(),
});
export type VoiceModel = z.infer<typeof voiceModelSchema>;

/** The session's model, voice and reasoning effort. */
export const voiceSelectionSchema = z.object({
  model: z.string().min(1),
  voice: z.string().min(1),
  effort: effortSchema.optional(),
});
export type VoiceSelection = z.infer<typeof voiceSelectionSchema>;

export const clientEventSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("session.start"),
    voice: z.string().min(1).optional(),
    instructions: z.string().optional(),
    model: z.string().min(1).optional(),
    effort: effortSchema.optional(),
  }),
  z.object({ type: z.literal("session.configure") }).merge(voiceSelectionSchema),
  z.object({
    type: z.literal("voice.preview"),
    requestId: z.string().min(1),
    model: z.string().min(1),
    voice: z.string().min(1),
  }),
  z.object({ type: z.literal("session.end") }),
  z.object({ type: z.literal("interrupt") }),
  z.object({
    type: z.literal("widget.action"),
    id: z.string().min(1),
    action: z.string().min(1),
    value: z.string().optional(),
  }),
]);
export type ClientEvent = z.infer<typeof clientEventSchema>;

export const serverEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("state"), state: osmoStateSchema, detail: z.string().optional() }),
  z.object({
    type: z.literal("transcript.delta"),
    role: z.enum(["user", "assistant"]),
    text: z.string(),
    itemId: z.string().optional(),
  }),
  // The spec's payload is (id, type, props); it is nested under `widget`
  // because `type` is already the event discriminator.
  z.object({
    type: z.literal("widget.render"),
    widget: z.object({ id: z.string(), type: z.string(), props: z.unknown() }),
  }),
  z.object({
    type: z.literal("widget.update"),
    id: z.string(),
    patch: z.record(z.unknown()),
  }),
  z.object({ type: z.literal("models"), models: z.array(voiceModelSchema), selection: voiceSelectionSchema }),
  z.object({ type: z.literal("voice.configured"), selection: voiceSelectionSchema, source: z.enum(["client", "tool"]) }),
  // Answer to a `voice.preview` request: base64 PCM16 (24 kHz mono, ~2 s) or an error.
  z.object({
    type: z.literal("voice.preview"),
    requestId: z.string(),
    model: z.string(),
    voice: z.string(),
    audio: z.string().optional(),
    error: z.string().optional(),
  }),
]);
export type ServerEvent = z.infer<typeof serverEventSchema>;
