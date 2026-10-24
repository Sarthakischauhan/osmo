import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WidgetView } from "../src/WidgetView";

const noop = () => undefined;

describe("WidgetView", () => {
  it("renders html widgets in a sandbox without same-origin", () => {
    const html = renderToStaticMarkup(
      <WidgetView id="w1" type="html" props={{ html: "<button>tip</button>" }} onAction={noop} />,
    );
    expect(html).toContain('sandbox="allow-scripts allow-forms"');
    expect(html).not.toContain("allow-same-origin");
  });

  it("escapes markdown instead of injecting it", () => {
    const html = renderToStaticMarkup(
      <WidgetView id="w2" type="card" props={{ title: "t", body: "**72°** <img src=x onerror=alert(1)>" }} onAction={noop} />,
    );
    expect(html).toContain("<strong>72°</strong>");
    expect(html).toContain("&lt;img");
  });

  it("drops widgets that fail the schema", () => {
    expect(renderToStaticMarkup(<WidgetView id="w3" type="chart" props={{ kind: "pie" }} onAction={noop} />)).toBe("");
  });

  it("numbers options like the canvas mock", () => {
    const html = renderToStaticMarkup(
      <WidgetView
        id="w4"
        type="options"
        props={{ prompt: "Pick a dinner spot", choices: [{ id: "r", label: "Ramen counter", detail: "0.8 mi" }] }}
        onAction={noop}
      />,
    );
    expect(html).toContain("say it or tap");
    expect(html).toContain("0.8 mi");
  });
});
