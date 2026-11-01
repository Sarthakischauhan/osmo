import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
const css = strip(readFileSync(new URL("../src/styles.css", import.meta.url), "utf8"));
const pickerCss = strip(readFileSync(new URL("../../../packages/osmo-composer/src/radial-picker/radialPicker.css", import.meta.url), "utf8"));
const button = readFileSync(new URL("../src/components/ui/button.tsx", import.meta.url), "utf8");

/** The `border-radius` declared by a rule that lists exactly this selector, with `var(--x)` resolved from the `:root` block. */
function radiusOf(source: string, selector: string): string | undefined {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rules = source.matchAll(new RegExp(`(?:^|[}\\n,])\\s*${escaped}\\s*(?:,[^{]*)?\\{([^}]*)\\}`, "gm"));
  const value = [...rules].map((rule) => /(?:^|[;\s])border-radius\s*:\s*([^;]+)/.exec(rule[1] ?? "")?.[1]?.trim()).find((found) => found !== undefined);
  const variable = /^var\((--[a-z-]+)\)$/.exec(value ?? "")?.[1];
  if (variable === undefined) return value;
  return new RegExp(`${variable}\\s*:\\s*([^;]+);`).exec(source)?.[1]?.trim();
}

// Sarthak, Oct 30: buttons 8px, the orb in a message fully round, the orb's preview window 8px. The radial
// picker's options stay circular. Everything else keeps the radius it had.
describe("border radii", () => {
  const buttons = [
    ".end",
    ".osmo-btn",
    ".osmo-icon-btn",
    ".osmo-icon-button",
    ".osmo-new-thread",
    ".osmo-thread-button",
    ".osmo-thread-action",
    ".osmo-list-row",
    ".desktop-support",
    ".osmo-appearance button",
    ".osmo-card-actions button",
    ".canvas.chat-root .chat-question button",
    ".canvas.chat-root .chat-subagent-inspect",
  ];

  it.each(buttons)("%s is 8px", (selector) => {
    expect(radiusOf(css, selector)).toBe("8px");
  });

  it("keeps a button's focus ring as round as the button", () => {
    expect(/\.osmo-thread-button:focus-visible[^{]*\{[^}]*border-radius:\s*var\(--radius-button\)/.test(css)).toBe(true);
  });

  it("gives the shared button component the same 8px", () => {
    expect(button).not.toMatch(/rounded-(md|sm|lg|xl|full)/);
    expect(button.match(/rounded-\[var\(--radius-button\)\]/g)?.length).toBe(5);
    expect(/--radius-button:\s*8px/.test(css)).toBe(true);
  });

  it("makes the orb in a message fully round", () => {
    expect(radiusOf(css, ".avatar")).toBe("50%");
  });

  it("makes the orb's preview window 8px", () => {
    expect(radiusOf(css, ".osmo-floating-box")).toBe("8px");
  });

  it("keeps the radial picker's options circular", () => {
    expect(/--rp-option-radius:\s*999px/.test(pickerCss)).toBe(true);
    expect(radiusOf(pickerCss, ".rp-option")).toBe("999px");
  });
});
