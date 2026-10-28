import { WidgetProvider, WidgetRenderer } from "@sarchauhan/chat";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { osmoWidgets, toWidgetPart } from "../src/registry";

const respond = async () => undefined;

function render(id: string, type: string, props: unknown): string {
  const part = toWidgetPart(id, type, props);
  if (!part) throw new Error(`${type} failed validation`);
  return renderToStaticMarkup(
    <WidgetProvider widgets={osmoWidgets} respondToWidget={respond}>
      <WidgetRenderer part={part} />
    </WidgetProvider>,
  );
}

describe("osmo widget registry on chat-sdk", () => {
  it("renders html widgets in a sandbox without same-origin", () => {
    const html = render("w1", "html", { html: "<button>tip</button>" });
    expect(html).toContain('sandbox="allow-scripts allow-forms"');
    expect(html).not.toContain("allow-same-origin");
  });

  it("renders cards in chat-sdk's BaseWidget with markdown, escaping raw html", () => {
    const html = render("w2", "card", { title: "Palo Alto", body: "**72°** <img src=x onerror=alert(1)>" });
    expect(html).toContain("chat-widget-card");
    expect(html).toContain("Palo Alto");
    expect(html).toContain("<strong>72°</strong>");
    expect(html).not.toContain("<img src=\"x\"");
  });

  it("renders options with chat-sdk's QuestionWidget", () => {
    const html = render("w4", "options", {
      prompt: "Pick a dinner spot",
      choices: [{ id: "r", label: "Ramen counter", detail: "0.8 mi" }],
    });
    expect(html).toContain("chat-question");
    expect(html).toContain("Ramen counter");
    expect(html).toContain("0.8 mi");
  });

  it("renders the local chart", () => {
    expect(render("w5", "chart", { kind: "line", series: [{ name: "TSLA", points: [{ x: "a", y: 1 }, { x: "b", y: 2 }] }] })).toContain(
      "osmo-chart",
    );
  });

  it("renders the agent browser: live with its latest frame, then ended on the last one", () => {
    const frame = "data:image/jpeg;base64,/9j/4AAQ";
    const live = render("w7", "browser", { status: "live", url: "https://example.com/", frame });
    expect(live).toContain('data-status="live"');
    expect(live).toContain(`src="${frame}"`);
    expect(live).toContain("https://example.com/");
    expect(live).toContain("Live");
    const ended = render("w7", "browser", { status: "ended", url: "https://example.com/", frame });
    expect(ended).toContain('data-status="ended"');
    expect(ended).toContain(`src="${frame}"`);
    expect(ended).toContain("Ended");
  });

  it("shows the agent browser as starting until its first frame", () => {
    expect(render("w8", "browser", { status: "live" })).toContain("Starting the browser");
  });

  it("drops widgets that fail the schema", () => {
    expect(toWidgetPart("w3", "chart", { kind: "pie" })).toBeNull();
    expect(toWidgetPart("w6", "unknown", {})).toBeNull();
  });
});
