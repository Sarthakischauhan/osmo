import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const RADIUS = /(--chat-radius(?:-lg)?|border(?:-[a-z]+)*-radius)\s*:\s*([^;}]+)/g;

// Corners are 2-3px everywhere in the app's own CSS. The radial picker's options are the one round thing,
// and they are styled in the composer package's radialPicker.css, not here.
describe("border radii in the app's styles", () => {
  const declarations = [...css.matchAll(RADIUS)].map((match) => ({ property: match[1] ?? "", value: (match[2] ?? "").trim() }));

  it("finds the declarations it checks", () => {
    expect(declarations.length).toBeGreaterThan(30);
    expect(declarations.map((d) => d.property)).toContain("--chat-radius");
  });

  it("has none above 3px", () => {
    const tooRound = declarations.filter(({ value }) => {
      if (value === "0" || /^var\(--radius, *3px\)$/.test(value) || value === "var(--radius)") return false;
      return !/^\d+(\.\d+)?px$/.test(value) || Number.parseFloat(value) > 3;
    });
    expect(tooRound).toEqual([]);
  });
});
