import { z } from "zod";

/**
 * Osmo wire protocol: one WebSocket between the browser and Symphony.
 * Binary frames carry PCM16 audio (24 kHz mono, little-endian) both ways.
 * Text frames carry the JSON events below. Nothing here mirrors Grok's
 * event format, so the composer can sit on top of any backend.
 */

export const AUDIO_SAMPLE_RATE = 24000;

export const osmoStateSchema = z.enum(["idle", "listening", "thinking", "speaking", "working"]);
export type OsmoState = z.infer<typeof osmoStateSchema>;

export const clientEventSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("session.start"),
    voice: z.string().min(1).optional(),
    instructions: z.string().optional(),
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
]);
export type ServerEvent = z.infer<typeof serverEventSchema>;
