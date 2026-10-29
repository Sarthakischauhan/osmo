import { describe, expect, it } from "vitest";
import { storedTheme } from "../src/appearance";

describe("a theme another window stored", () => {
  it("is followed only when it is a theme", () => {
    expect(storedTheme("light")).toBe("light");
    expect(storedTheme("dark")).toBe("dark");
    for (const value of [null, "", "system", "Light", "{}"]) expect(storedTheme(value)).toBeUndefined();
  });
});
