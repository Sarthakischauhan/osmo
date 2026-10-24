# RadialPicker

A hover/scroll dial around the orb for choosing **provider → model → voice + reasoning effort**.
The picker only calls `onChange`; the app sends `session.configure {model, voice, effort}` over
the existing Osmo WebSocket (`useOsmoSession().configure`). Try it at **`/dev/picker`** in `apps/web`.

```tsx
import { RadialPicker, usePersistedPick, useOsmoSession } from "@osmo/osmo-composer";
import "@osmo/osmo-composer/radial-picker.css";

const session = useOsmoSession({ url, voice: pick?.voice, model: pick?.model, effort: pick?.effort });
const [pick, save] = usePersistedPick(session.voiceModels); // localStorage "osmo.voice-pick"
<RadialPicker
  registry={session.voiceModels}            // from Symphony's `models` event
  value={pick}
  onChange={(next) => { save(next); session.configure(next); }}
  onPreview={session.previewVoice}
  onPreviewStop={session.stopPreview}
  previewing={session.previewing}
  previewDisabled={session.state === "speaking"}
/>;
```

## Props

| prop | type | default | notes |
| --- | --- | --- | --- |
| `registry` | `VoiceModel[]` | — | Grouped into providers by `provider`, in first-seen order. |
| `value` | `{ model, voice, effort? }` | — | Selected path; stays highlighted when the dial reopens. |
| `onChange` | `(next) => void` | — | Exactly once per commit (click / Enter / Space). Picking a model picks its `defaultVoice` and keeps the effort if the model still offers it. |
| `orbSize` | `number` | `96` | Orb diameter in px. |
| `providerIcons` | `Record<string, ProviderIcon>` | built-in map | See below. |
| `orb` | `ReactNode` | neutral glass sphere | Orb face inside the button. |
| `onPreview` | `({ model, voice }) => void` | — | ~400 ms after a voice pill is hovered (or keyboard-focused). With reduced motion, hover never auto-plays; focus still does. |
| `onPreviewStop` | `() => void` | — | Hovered/focused voice changed or the dial closed. |
| `previewing` | `{ model, voice }` | — | Shows the playing indicator on that pill. |
| `previewDisabled` | `boolean` | `false` | E.g. while the agent is speaking. |

`VoiceModel = { id, provider, label, capabilities: { tools, interrupt, customVoices, reasoningEffort?: Effort[] }, voices: { id, label, traits }[], defaultVoice }`, `Effort = "low" | "medium" | "high"`.

## `providerIcons`

Keyed by `VoiceModel.provider`; each entry is `{ label, icon? }`. `label` is used in the summary
pill and tooltip; `icon` is a ~20px monochrome glyph in `currentColor`. A provider without an entry
(or without `icon`) gets a monogram of its label/id.

| key | label |
| --- | --- |
| `xai` | xAI |
| `openai` | OpenAI |
| `google` | Google |
| `anthropic` | Anthropic |
| `elevenlabs` | ElevenLabs |
| `deepgram` | Deepgram |
| `cartesia` | Cartesia |

```ts
import { providerIcons } from "@osmo/osmo-composer";
const icons = { ...providerIcons, acme: { label: "Acme", icon: <AcmeGlyph /> } };
```

## Behaviour

- Rings (angles CCW from +x): R1 providers r=80, 30–150°, 5 slots × 30°; R2 models r=160, 20–160°, 5 × 35°;
  R3 voices r=235, 130–170°, 3 × 20° (left); R3 effort r=235, 14–46°, 3 × 16° (right, only levels in `reasoningEffort`, hidden if none).
- Hover orb → R1, provider → R2, model → R3. Leaving the dial closes it after 250 ms; Escape closes and refocuses the orb.
- Hit-testing is by radius from the orb centre (R1 56–120, R2 120–198, R3 198–275; R3 side by angle).
- Scroll with wheel/trackpad (`deltaY + deltaX`), touch angular drag, or ←/→ in a focused ring. Offsets are fractional,
  snap 120 ms after input stops with a 180 ms ease-out, clamp at both ends, and never wrap. Edge items fade/scale over the last
  half-slot, with `…` where more items exist. While a ring scrolls or settles, outer rings don't open or switch until 150 ms after the snap.
  `prefers-reduced-motion` jumps whole slots with no glide.
- Pure pieces: `geometry.ts` (slot → angle → xy, hit bands, edge falloff, clamp/snap), `reducer.ts` (open/hover/scroll/freeze), `persistence.ts` (`restorePick`).

## Theme

White alphas on `#0A0A0A`, no tint. Pills: idle fill 0.04 / text `#B5B5B5`, hover 0.09 / `#FFF`, selected 0.16 / `#FFF`, `backdrop-filter: blur(14px)`.
Following Osmo's no-lines rule there are **no borders and no arc tracks**: state is fill plus a soft glow, and arcs are implied
by spacing. To restore the original spec's lines, add `box-shadow: inset 0 0 0 1px rgba(255,255,255,.12 | .25 | .45)` to the
idle/hover/selected `.rp-item` rules in `radialPicker.css`.
