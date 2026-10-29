import { type ReactNode, useEffect, useState } from "react";

/**
 * Pieces the widgets with a framed viewport share (html, agent browser). The markup stays in each widget;
 * the look is the `.osmo-frame*` rules in the web app's stylesheet.
 */

export function Icon({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

export const ExpandIcon = () => (
  <Icon>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </Icon>
);
export const CloseIcon = () => (
  <Icon>
    <path d="M6 6l12 12M18 6L6 18" />
  </Icon>
);

/** The viewport height of a framed widget in the feed; fullscreen fills the modal instead. */
export const INLINE_HEIGHT = 320;

/** Fullscreen state for a framed widget; Escape leaves it. */
export function useFullscreen(): [boolean, (open: boolean) => void] {
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);
  return [fullscreen, setFullscreen];
}
