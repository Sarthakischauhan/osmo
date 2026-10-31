import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
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

const src = new URL("../src/", import.meta.url).pathname;
const sources = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((entry) => (entry.isDirectory() ? sources(join(dir, entry.name)) : /\.tsx$/.test(entry.name) ? [join(dir, entry.name)] : []));

// Sarthak, Oct 30: no visible scrollbars; scrolling itself keeps working. One shared class does it.
describe("hidden scrollbars", () => {
  it("has one shared rule that hides the bar in both engines and leaves scrolling alone", () => {
    expect(rule(".osmo-scroll")).toMatch(/scrollbar-width: none;/);
    expect(css).toMatch(/\.osmo-scroll::-webkit-scrollbar \{ display: none; \}/);
    expect(rule(".osmo-scroll")).not.toMatch(/overflow/);
  });

  it("is on the preview box, which scrolls", () => {
    expect(rule(".osmo-floating-box")).toMatch(/overflow-y: auto;/);
    expect(readFileSync(new URL("../src/FloatingOrb.tsx", import.meta.url), "utf8")).toContain('className="osmo-floating-box osmo-scroll"');
  });

  it("is on the workspace feed", () => {
    expect(rule(".canvas.chat-root")).toMatch(/overflow-y: auto;/);
    expect(readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8")).toContain('className="canvas chat-root osmo-scroll"');
  });

  it("is on every Tailwind scroll container, such as the thread list, menus and the command list", () => {
    const missing = sources(src).flatMap((file) =>
      [...readFileSync(file, "utf8").matchAll(/"([^"\n]*\boverflow-(?:y-)?auto\b[^"\n]*)"/g)].filter((match) => !(match[1] ?? "").split(/\s+/).includes("osmo-scroll")).map((match) => `${file.slice(src.length)}: ${match[1]}`),
    );
    expect(missing).toEqual([]);
    expect(sources(src).filter((file) => /\bosmo-scroll\b/.test(readFileSync(file, "utf8"))).map((file) => file.slice(src.length))).toEqual(
      expect.arrayContaining(["components/ui/sidebar.tsx", "components/ui/command.tsx", "components/ui/dropdown-menu.tsx", "components/ui/context-menu.tsx"]),
    );
  });
});
