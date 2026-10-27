/** What each ring's options say: text, hover title and content, per ring. */
import type { ReactNode } from "react";
import type { Effort, VoiceModel, VoiceSelection } from "../protocol";

type Voice = VoiceModel["voices"][number];
import type { RingKey } from "./geometry";
import { type ProviderIcon, ProviderMark, providerIcon } from "./providerIcons";
import type { ProviderGroup } from "./reducer";

export interface OptionLabel {
  text: string;
  title: string;
  node: ReactNode;
  /** Shown as a tooltip instead of the native title (the model's full id). */
  tip?: string;
}

export const PICKER_LABEL = "Change provider, model, voice and reasoning";

export const RING_LABELS: Record<RingKey, string> = {
  providers: "Providers",
  models: "Models",
  voices: "Voices",
  effort: "Reasoning effort",
};

const EFFORT_LABEL: Record<Effort, string> = {
  none: "Off",
  minimal: "Min",
  low: "Low",
  medium: "Med",
  high: "High",
  xhigh: "Max",
};
const EFFORT_BARS: Record<Effort, number> = { none: 0, minimal: 1, low: 1, medium: 2, high: 3, xhigh: 3 };

/** The model ring's text: the registry's short label, else its label. */
export function modelRingLabel(model: Pick<VoiceModel, "label" | "shortLabel">): string {
  return model.shortLabel ?? model.label;
}

/** A provider chip: its logo, and why it is locked when it is. Unknown providers use the server's label. */
export function providerLabel(group: ProviderGroup | undefined, icons: Record<string, ProviderIcon>): OptionLabel {
  const entry = icons[group?.id ?? ""] ?? providerIcon(icons, group?.label ?? group?.id ?? "");
  const title = group?.reason ? `${entry.label} · ${group.reason}` : entry.label;
  return { text: entry.label, title, node: <ProviderMark entry={entry} /> };
}

export function modelLabel(model: VoiceModel | undefined): OptionLabel {
  const text = model ? modelRingLabel(model) : "";
  const tip = model?.capabilities.speaks === false ? `${model.id} · transcription only` : model?.id;
  return { text, title: model?.id ?? "", node: text, tip };
}

/** Hover title: the label, plus its traits when the catalog publishes any. */
function voiceTitle(voice: Voice | undefined): string {
  if (voice === undefined) return "";
  return voice.traits.length > 0 ? `${voice.label} · ${voice.traits.join(", ")}` : voice.label;
}

export function voiceLabel(voice: Voice | undefined, playing: boolean): OptionLabel {
  return {
    text: voice?.label ?? "",
    title: voiceTitle(voice),
    node: (
      <>
        {voice?.label}
        {playing && (
          <span className="rp-playing" aria-label="previewing">
            <i />
            <i />
            <i />
          </span>
        )}
      </>
    ),
  };
}

export function effortLabel(level: Effort): OptionLabel {
  return {
    text: EFFORT_LABEL[level],
    title: `Reasoning effort: ${level}`,
    node: (
      <>
        {EFFORT_LABEL[level]}
        <span className="rp-meter" aria-hidden>
          {[1, 2, 3].map((bar) => (
            <i key={bar} data-on={bar <= EFFORT_BARS[level]} />
          ))}
        </span>
      </>
    ),
  };
}

/** The status line under the orb: provider · model · voice · effort. */
export function pickSummary(registry: readonly VoiceModel[], value: VoiceSelection, icons: Record<string, ProviderIcon>): string {
  const model = registry.find((m) => m.id === value.model);
  return [model ? providerIcon(icons, model.provider).label : undefined, value.model, model?.voices.find((v) => v.id === value.voice)?.label ?? value.voice, value.effort]
    .filter((part) => part !== undefined)
    .join(" · ");
}
