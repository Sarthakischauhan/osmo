import { useEffect } from "react";

type Shortcuts = {
  /** ⌘K / Ctrl+K: a new thread (Home). */
  newThread: () => void;
  /** Space, while no call is connected: start one. */
  start: () => void;
  /** Space, while Osmo speaks: cut it off. */
  interrupt: () => void;
  disconnected: boolean;
  speaking: boolean;
};

/** ⌘K: a new thread; Space: talk, or interrupt while Osmo speaks (not while typing in a field). */
export function useKeyboardShortcuts({ newThread, start, interrupt, disconnected, speaking }: Shortcuts): void {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        newThread();
        return;
      }
      const target = event.target;
      const typing = target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "BUTTON", "SELECT"].includes(target.tagName));
      if (event.code !== "Space" || typing || event.repeat) return;
      event.preventDefault();
      if (disconnected) start();
      else if (speaking) interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newThread, start, interrupt, disconnected, speaking]);
}
