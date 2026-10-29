import { createContext } from "react";

/**
 * What the user does to the agent browser in its fullscreen view (the composer's `browser.*` client events, less the widget id). `x` and `y` are where, as fractions (0 to 1)
 * of the displayed page; `key`, `code` and `keyCode` are those of the DOM KeyboardEvent; the deltas those of the WheelEvent.
 */
export type BrowserInput =
  | { type: "browser.click"; x: number; y: number }
  | { type: "browser.scroll"; x: number; y: number; deltaX: number; deltaY: number }
  | { type: "browser.key"; key: string; code: string; keyCode: number };

/** How the app passes the user's input on an agent browser (by widget id) to the server, and closes it. */
export interface BrowserControls {
  input: (widgetId: string, input: BrowserInput) => void;
  close: (widgetId: string) => void;
}

export const BrowserControlsContext = createContext<BrowserControls | undefined>(undefined);
