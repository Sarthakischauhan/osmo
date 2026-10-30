// @vitest-environment jsdom
import { WidgetProvider, WidgetRenderer } from "@sarchauhan/chat";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserControlsContext, type BrowserControls } from "../src/controls";
import { osmoWidgets, toWidgetPart } from "../src/registry";

afterEach(cleanup);

const FRAME = "data:image/jpeg;base64,/9j/4AAQ";

function shown(controls: BrowserControls | undefined, status: "live" | "ended", disabled = false) {
  const part = toWidgetPart("w-browser", "browser", { status, url: "https://example.com/", frame: FRAME });
  if (!part) throw new Error("browser failed validation");
  return render(
    <WidgetProvider widgets={osmoWidgets} respondToWidget={async () => undefined} disabled={disabled}>
      <BrowserControlsContext.Provider value={controls}>
        <WidgetRenderer part={part} />
      </BrowserControlsContext.Provider>
    </WidgetProvider>,
  );
}

describe("the agent browser in the widget registry", () => {
  it("hands the user's input and its close button to the app, with the widget's id", () => {
    const controls = { input: vi.fn(), close: vi.fn() };
    shown(controls, "live");
    fireEvent.click(screen.getByRole("button", { name: "Close browser" }));
    expect(controls.close).toHaveBeenCalledWith("w-browser");

    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    const page = screen.getByRole("dialog").querySelector<HTMLElement>(".osmo-frame-viewport");
    if (!page) throw new Error("no page");
    fireEvent.keyDown(page, { key: "Enter", code: "Enter", keyCode: 13 });
    expect(controls.input).toHaveBeenCalledWith("w-browser", { type: "browser.key", key: "Enter", code: "Enter", keyCode: 13 });
  });

  it("is a view only without an app to send to", () => {
    shown(undefined, "live");
    expect(screen.queryByRole("button", { name: "Close browser" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    expect(screen.getByRole("dialog").querySelector(".osmo-frame-viewport")?.getAttribute("tabindex")).toBeNull();
  });

  it("is a view only while the app is not connected", () => {
    shown({ input: vi.fn(), close: vi.fn() }, "live", true);
    expect(screen.queryByRole("button", { name: "Close browser" })).toBeNull();
  });
});
