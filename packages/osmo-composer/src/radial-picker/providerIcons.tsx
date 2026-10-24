import { useState } from "react";

export interface ProviderIcon {
  /** Shown in the summary pill and the R1 tooltip. */
  label: string;
  /** Logo URL loaded at runtime and drawn monochrome white; omitted or failing → monogram of `label`. */
  logo?: string;
}

const MODELS_DEV = "https://models.dev/logos";

/**
 * Default provider entries, keyed by `VoiceModel.provider`. Logos come from
 * models.dev at runtime (nothing is bundled). Only slugs models.dev actually has
 * get a URL: for unknown slugs it answers 200 with a generic placeholder, which
 * `onError` can't catch, so those go straight to the monogram.
 */
export const providerIcons: Record<string, ProviderIcon> = {
  xai: { label: "xAI", logo: `${MODELS_DEV}/xai.svg` },
  openai: { label: "OpenAI", logo: `${MODELS_DEV}/openai.svg` },
  google: { label: "Google", logo: `${MODELS_DEV}/google.svg` },
  anthropic: { label: "Anthropic", logo: `${MODELS_DEV}/anthropic.svg` },
  mistral: { label: "Mistral", logo: `${MODELS_DEV}/mistral.svg` },
  elevenlabs: { label: "ElevenLabs" },
  deepgram: { label: "Deepgram" },
  cartesia: { label: "Cartesia" },
};

/** The entry for a provider, falling back to its id as label (and so a monogram). */
export function providerIcon(icons: Record<string, ProviderIcon>, provider: string): ProviderIcon {
  return icons[provider] ?? { label: provider };
}

/** A provider's logo in white, or its monogram when there is no logo or it fails to load. */
export function ProviderMark({ entry }: { entry: ProviderIcon }) {
  const [failed, setFailed] = useState<string | undefined>();
  if (entry.logo === undefined || failed === entry.logo) {
    return <span className="rp-monogram">{entry.label.slice(0, 1).toUpperCase()}</span>;
  }
  const logo = entry.logo;
  return <img className="rp-logo" src={logo} alt="" width={20} height={20} draggable={false} onError={() => setFailed(logo)} />;
}
