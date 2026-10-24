import { useEffect, useState } from "react";

export interface ProviderIcon {
  /** Shown in the summary pill and the R1 tooltip. */
  label: string;
  /** Logo URL tried first, at runtime, drawn monochrome white. */
  logo?: string;
  /** Bundled monochrome logo used when `logo` fails or is a placeholder. Neither → monogram of `label`. */
  fallback?: string;
}

const MODELS_DEV = "https://models.dev/logos";

/**
 * models.dev answers unknown slugs with 200 and a generic "sparkles" icon instead of an error,
 * so a runtime logo is fetched once and checked against that placeholder's first path.
 */
export const MODELS_DEV_PLACEHOLDER_MARK = "M9.8132 15.9038";

export function isPlaceholderLogo(svg: string): boolean {
  return svg.includes(MODELS_DEV_PLACEHOLDER_MARK);
}

const bundled = (slug: string) => new URL(`./logos/${slug}.svg`, import.meta.url).href;

/**
 * Default provider entries, keyed by `VoiceModel.provider`. Every provider first tries its
 * models.dev logo at runtime; the five models.dev actually has are also bundled (monochrome,
 * `currentColor`) as the fallback. ElevenLabs, Deepgram and Cartesia get models.dev's placeholder,
 * have no bundled logo, and so show a monogram.
 */
export const providerIcons: Record<string, ProviderIcon> = {
  xai: { label: "xAI", logo: `${MODELS_DEV}/xai.svg`, fallback: bundled("xai") },
  openai: { label: "OpenAI", logo: `${MODELS_DEV}/openai.svg`, fallback: bundled("openai") },
  google: { label: "Google", logo: `${MODELS_DEV}/google.svg`, fallback: bundled("google") },
  anthropic: { label: "Anthropic", logo: `${MODELS_DEV}/anthropic.svg`, fallback: bundled("anthropic") },
  mistral: { label: "Mistral", logo: `${MODELS_DEV}/mistral.svg`, fallback: bundled("mistral") },
  elevenlabs: { label: "ElevenLabs", logo: `${MODELS_DEV}/elevenlabs.svg` },
  deepgram: { label: "Deepgram", logo: `${MODELS_DEV}/deepgram.svg` },
  cartesia: { label: "Cartesia", logo: `${MODELS_DEV}/cartesia.svg` },
};

/** The entry for a provider, falling back to its id as label (and so a monogram). */
export function providerIcon(icons: Record<string, ProviderIcon>, provider: string): ProviderIcon {
  return icons[provider] ?? { label: provider };
}

export type LogoFetch = (url: string) => Promise<{ ok: boolean; text: () => Promise<string> }>;

const checked = new Map<string, Promise<boolean>>();

/** Whether `url` serves a real logo (not an error, not models.dev's placeholder). Memoised per URL. */
export function runtimeLogoUsable(url: string, fetcher: LogoFetch = (u) => fetch(u)): Promise<boolean> {
  let result = checked.get(url);
  if (result === undefined) {
    result = fetcher(url)
      .then(async (response) => response.ok && !isPlaceholderLogo(await response.text()))
      .catch(() => false);
    checked.set(url, result);
  }
  return result;
}

/** Forget memoised checks (tests). */
export function resetLogoChecks(): void {
  checked.clear();
}

/**
 * The image to draw for `entry`: runtime logo → bundled fallback → none (monogram).
 * `failed` lists sources whose <img> already errored.
 */
export async function resolveLogo(
  entry: ProviderIcon,
  failed: ReadonlySet<string> = new Set(),
  fetcher?: LogoFetch,
): Promise<string | undefined> {
  if (entry.logo !== undefined && !failed.has(entry.logo) && (await runtimeLogoUsable(entry.logo, fetcher))) return entry.logo;
  if (entry.fallback !== undefined && !failed.has(entry.fallback)) return entry.fallback;
  return undefined;
}

/** A provider's logo in white (runtime, else bundled), or its monogram when neither is usable. */
export function ProviderMark({ entry }: { entry: ProviderIcon }) {
  const [failed, setFailed] = useState<ReadonlySet<string>>(() => new Set());
  const [src, setSrc] = useState<string | undefined>();
  const [resolved, setResolved] = useState(false);
  const { logo, fallback } = entry;
  useEffect(() => {
    let live = true;
    setResolved(false);
    void resolveLogo({ label: "", logo, fallback }, failed).then((next) => {
      if (!live) return;
      setSrc(next);
      setResolved(true);
    });
    return () => {
      live = false;
    };
  }, [logo, fallback, failed]);
  if (!resolved) return <span className="rp-monogram" data-pending />;
  if (src === undefined) return <span className="rp-monogram">{entry.label.slice(0, 1).toUpperCase()}</span>;
  return (
    <img
      className="rp-logo"
      src={src}
      data-source={src === entry.logo ? "runtime" : "bundled"}
      alt=""
      width={20}
      height={20}
      draggable={false}
      onError={() => setFailed((previous) => new Set(previous).add(src))}
    />
  );
}
