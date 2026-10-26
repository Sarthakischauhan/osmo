import { describe, expect, it } from "vitest";
import { describeWidget, widgetSchema } from "../src/schema";

describe("widgetSchema", () => {
  it("accepts a valid options widget and describes it", () => {
    const parsed = widgetSchema.parse({
      type: "options",
      props: {
        prompt: "Pick a dinner spot",
        choices: [
          { id: "a", label: "Italian, wood-fired", detail: "0.4 mi" },
          { id: "b", label: "Ramen counter" },
        ],
      },
    });
    expect(describeWidget(parsed)).toBe(
      'options "Pick a dinner spot", 2 choices: 1. Italian, wood-fired; 2. Ramen counter',
    );
  });

  it("rejects unknown props and unknown types", () => {
    expect(widgetSchema.safeParse({ type: "card", props: { title: "x", body: "y", extra: 1 } }).success).toBe(false);
    expect(widgetSchema.safeParse({ type: "video", props: {} }).success).toBe(false);
  });

  it("accepts http(s) and inline picture card images, nothing else", () => {
    const card = (image: string) => widgetSchema.safeParse({ type: "card", props: { title: "x", body: "y", image } }).success;
    expect(card("https://example.com/a.png")).toBe(true);
    expect(card("data:image/jpeg;base64,/9j/4AAQ")).toBe(true);
    expect(card("javascript:alert(1)")).toBe(false);
    expect(card("data:text/html;base64,PGI+")).toBe(false);
  });

  it("rejects non-finite chart values", () => {
    const bad = { type: "chart", props: { kind: "line", series: [{ name: "TSLA", points: [{ x: "d1", y: Number.NaN }] }] } };
    expect(widgetSchema.safeParse(bad).success).toBe(false);
  });

  it("validates the optional html title and describes it", () => {
    const parsed = widgetSchema.parse({ type: "html", props: { html: "<p>x</p>", title: "Tip calculator" } });
    expect(describeWidget(parsed)).toBe('interactive html widget "Tip calculator"');
    expect(widgetSchema.safeParse({ type: "html", props: { html: "<p>x</p>", title: "" } }).success).toBe(false);
    expect(widgetSchema.safeParse({ type: "html", props: { html: "<p>x</p>", title: "t".repeat(81) } }).success).toBe(false);
  });
});
