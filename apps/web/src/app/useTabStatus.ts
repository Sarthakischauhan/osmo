import { useEffect } from "react";
import type { OrbMode } from "@osmo/osmo-composer";
import type { Theme } from "@/appearance";

const LABELS: Record<OrbMode, string> = {
  connecting: "Connecting",
  idle: "Ready",
  listening: "Listening",
  awaiting: "Your choice",
  thinking: "Thinking",
  speaking: "Speaking",
};
const OPACITY: Record<OrbMode, number> = { connecting: 0.4, idle: 0.6, listening: 1, awaiting: 1, thinking: 0.8, speaking: 1 };

/** The tab's title and favicon follow the orb, so a background tab shows what Osmo is doing. */
export function useTabStatus(mode: OrbMode, theme: Theme): void {
  useEffect(() => {
    const fill = theme === "light" ? "#171717" : "#ededed";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="24" fill="${fill}" opacity="${OPACITY[mode]}"/></svg>`;
    const icon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    const previousTitle = document.title;
    const previousHref = icon?.href;
    document.title = `Osmo · ${LABELS[mode]}`;
    if (icon) icon.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    return () => {
      document.title = previousTitle;
      if (icon && previousHref) icon.href = previousHref;
    };
  }, [mode, theme]);
}
