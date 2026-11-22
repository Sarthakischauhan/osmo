import { useEffect, useState } from "react";

/** The main pane's screens; `#notes` (etc.) in the URL opens one directly. */
export type AppView = "chat" | "tasks" | "calendar" | "notes";
const VIEWS: readonly AppView[] = ["chat", "tasks", "calendar", "notes"];

/** The screen the URL's hash names, or the conversation. */
export function initialView(): AppView {
  const hash = window.location.hash.slice(1);
  return VIEWS.find((view) => view === hash) ?? "chat";
}

/** The open screen, following the hash: the desktop tray's "Open calendar" changes the hash of the open page. */
export function useAppView(): [AppView, (view: AppView) => void] {
  const [view, setView] = useState<AppView>(initialView);
  useEffect(() => {
    const follow = () => setView(initialView());
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);
  return [view, setView];
}
