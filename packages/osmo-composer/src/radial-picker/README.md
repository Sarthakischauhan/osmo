# RadialPicker

A hover/scroll dial around the orb for choosing **provider → model → voice + reasoning effort**.
The picker only calls `onChange`; the app sends `session.configure {model, voice, effort}` over
the existing Osmo WebSocket (`useOsmoSession().configure`). In `apps/web` it wraps the main orb (`OrbComposer`'s `picker` prop).

```tsx
import { RadialPicker, usePersistedPick, useOsmoSession } from "@osmo/osmo-composer";
import "@osmo/osmo-composer/radial-picker.css";

const session = useOsmoSession({ url, voice: pick?.voice, model: pick?.model, effort: pick?.effort });
// `choose` shows a pick; `save` also writes localStorage "osmo.voice-pick" — call it on `voice.configured`.
const [pick, save, choose] = usePersistedPick(session.voiceModels);
<RadialPicker
  registry={session.voiceModels}            // from Symphony's `models` event
  value={pick}
  onChange={(next) => { choose(next); session.configure(next); }}
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
| `onOrbActivate` | `() => void` | — | Orb click runs this (e.g. start talking); the dial then opens on hover/focus only. |
| `orbLabel` | `string` | — | Accessible name for the orb's own action, prefixed to the picker label. |

`VoiceModel = { id, provider, label, capabilities: { tools, interrupt, customVoices, reasoningEffort?: Effort[] }, voices: { id, label, traits }[], defaultVoice }`, `Effort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh"` (every value the core_ai voice catalog's sources publish; xAI documents `"high"` or `"none"`).

## Model labels

The model ring shows `VoiceModel.shortLabel` (e.g. "Latest", "3.0"), else `label`, untruncated; hovering or keyboard-focusing a model shows its full id in a tooltip.

## `providerIcons`

Keyed by `VoiceModel.provider`; each entry is `{ label, logo?, fallback? }`. `label` is used in the
summary pill and tooltip. Logos are drawn pure white (`filter: brightness(0) invert(1)`). The chain is:

1. `logo`, fetched at runtime (by default `https://models.dev/logos/<slug>.svg` for every provider);
   used unless the request fails, the `<img>` errors, or the SVG is models.dev's generic placeholder
   (it answers unknown slugs with 200 and a "sparkles" icon, detected by `isPlaceholderLogo`);
2. `fallback`, a bundled monochrome (`currentColor`) copy in `logos/` — only for the slugs models.dev
   really has: `xai`, `openai`, `google`, `anthropic`, `mistral`;
3. otherwise a monogram of `label` (today: ElevenLabs, Deepgram, Cartesia).

```ts
import { providerIcons } from "@osmo/osmo-composer";
const icons = { ...providerIcons, acme: { label: "Acme", logo: "https://cdn.example.com/acme.svg" } };
```

## Behaviour

- Rings (angles CCW from +x): R1 providers r=84, 30–150°, 4 slots × 40° (fewer providers than slots are spread evenly around 90°); R2 models r=160, 20–160°, 5 × 35°;
  R3 voices r=235, 130–170°, 3 × 20° (left); R3 effort r=235, 14–46°, 3 × 16° (right, only levels in `reasoningEffort`, hidden if none).
- Hover orb → R1, provider → R2, model → R3. Leaving the dial closes it after 250 ms; Escape closes and refocuses the orb.
- Hit-testing is by radius from the orb centre (R1 56–122, R2 122–198, R3 198–274; R3 side by angle; ring radii 84/160/236 = 84 + n × `--rp-ring-spacing`).
- Scroll with wheel/trackpad (`deltaY + deltaX`), touch angular drag, or ←/→ in a focused ring. Offsets are fractional,
  snap 120 ms after input stops with a 180 ms ease-out, clamp at both ends, and never wrap. Edge items fade/scale over the last
  half-slot, with `…` where more items exist. While a ring scrolls or settles, outer rings don't open or switch until 150 ms after the snap.
  `prefers-reduced-motion` jumps whole slots with no glide.
- Pure pieces: `geometry.ts` (slot → angle → xy, hit bands, edge falloff, clamp/snap), `reducer.ts` (open/hover/scroll/freeze), `persistence.ts` (`restorePick`).

## Theme

White alphas on `#0A0A0A`, no tint. Pills: idle fill 0.04 / text `#B5B5B5`, hover 0.09 / `#FFF`, selected 0.16 / `#FFF`, `backdrop-filter: blur(14px)`.
Following Osmo's no-lines rule there are **no borders and no arc tracks**: state is fill plus a soft glow, and arcs are implied
by spacing. To restore the original spec's lines, add `box-shadow: inset 0 0 0 1px rgba(255,255,255,.12 | .25 | .45)` to the
idle/hover/selected `.rp-option` rules in `radialPicker.css`.

## Option tokens

Every option in every ring (provider logo chips, models, voices, effort) is one primitive: `RadialOption`
(`option.tsx`) rendering one class, `.rp-option`, tagged only with `data-ring`. There are **no per-ring size
rules**; sizes come from these tokens on `.rp`. `OPTION_TOKENS` in `geometry.ts` is the source: `.rp` gets them
inline and the CSS defaults mirror them (a unit test keeps the two equal).

| Token | Value | Meaning |
| --- | --- | --- |
| `--rp-option-height` | 36px | visual height of every option |
| `--rp-option-padding-x` | 12px | horizontal padding of every option |
| `--rp-option-radius` | 3px | corner radius |
| `--rp-option-gap` | 6px | gap between label and glyph (logo, meter, playing bars) |
| `--rp-option-font-size` | 12px | label size |
| `--rp-option-hit` | 44px | minimum hit height; `::before` extends the 36px pill to it |
| `--rp-ring-spacing` | 76px | radial distance between rings (radii 84 / 160 / 236) |

Provider chips are the same 36px pill with a 20px logo (or 20px-wide monogram) inside the same padding.
`apps/web/e2e/unified-options-shots.ts` fails if computed height or padding differs between any two rings.

## In Osmo

The main app passes `picker` to `OrbComposer`, which renders this dial around its `GlassOrb`
(the canvas orb that follows playback level and turns amber while a choice is pending). The orb keeps
its tap: start talking, or interrupt while speaking (`onOrbActivate`); the dial opens on hover or focus.
A commit sends `session.configure`: Symphony applies voice and effort with `session.update` on the
live Grok session and hot-swaps the upstream connection for a model change, carrying the conversation over.
