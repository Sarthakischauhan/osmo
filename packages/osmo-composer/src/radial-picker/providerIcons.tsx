import type { ReactNode } from "react";

export interface ProviderIcon {
  /** Shown in the summary pill and the R1 tooltip. */
  label: string;
  /** Monochrome glyph drawn in currentColor, about 20px; omitted → monogram of `label`. */
  icon?: ReactNode;
}

const glyph = (children: ReactNode) => (
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden>
    {children}
  </svg>
);

/** Default provider glyphs, keyed by `VoiceModel.provider`. Simple geometric marks, not brand logos. */
export const providerIcons: Record<string, ProviderIcon> = {
  xai: { label: "xAI", icon: glyph(<path d="M4 4h3.2l12.8 16h-3.2zM16.8 4H20l-5.4 6.8-1.6-2zM4 20l5.4-6.8 1.6 2L7.2 20z" />) },
  openai: { label: "OpenAI", icon: glyph(<path d="M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9zm0 3.2L7 9.1v5.8l5 2.9 5-2.9V9.1z" />) },
  google: { label: "Google", icon: glyph(<path d="M12 2l2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6z" />) },
  anthropic: { label: "Anthropic", icon: glyph(<path d="M9.5 4h2.6L18 20h-2.7l-1.3-3.6H8.1L6.8 20H4.2zm-.5 9.8h4.1L11 8.2z" />) },
  elevenlabs: { label: "ElevenLabs", icon: glyph(<path d="M8 4h3v16H8zm5 0h3v16h-3z" />) },
  deepgram: { label: "Deepgram", icon: glyph(<path d="M5 4h7a8 8 0 010 16H5zm3 3v10h4a5 5 0 000-10z" />) },
  cartesia: { label: "Cartesia", icon: glyph(<path d="M4 11h16v2H4zm7-7h2v16h-2z" />) },
};

/** The entry for a provider, falling back to its id as label (and so a monogram). */
export function providerIcon(icons: Record<string, ProviderIcon>, provider: string): ProviderIcon {
  return icons[provider] ?? { label: provider };
}
