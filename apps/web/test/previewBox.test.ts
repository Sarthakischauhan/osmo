import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const windowState = readFileSync(new URL("../../desktop/src/window-state.js", import.meta.url), "utf8");
const rule = (selector: string): string => new RegExp(`^${selector.replace(/[.[\]]/g, "\\$&")} \\{([^}]*)\\}`, "m").exec(css)?.[1] ?? "";

// The orb window is as tall as the box reports itself: a box that scrolls must not be shrunk to the room the window
// has before it grows, or it would report that smaller height and stay there.
describe("the orb's preview box", () => {
  it("is never shrunk by the window around it", () => {
    expect(rule(".osmo-floating-box")).toMatch(/flex: none;/);
  });

  it("is as tall as the window's largest box, and scrolls beyond it", () => {
    const largest = /export const BOX_MAX_HEIGHT = (\d+);/.exec(windowState)?.[1];
    expect(rule(".osmo-floating-box")).toContain(`max-height: ${largest}px;`);
    expect(rule(".osmo-floating-box")).toMatch(/overflow-y: auto;/);
    expect(rule(".osmo-floating-box")).toContain("box-sizing: border-box;");
  });

  it("keeps the width the window opens at", () => {
    const width = /export const BOX_WIDTH = (\d+);/.exec(windowState)?.[1];
    expect(rule(".osmo-floating-box")).toContain(`width: ${width}px;`);
  });
});
