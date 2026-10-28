// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BrowserWidget } from "../src/browser";
import { HtmlWidget } from "../src/html";

afterEach(cleanup);

const FRAME = "data:image/jpeg;base64,/9j/4AAQ";

describe("BrowserWidget", () => {
  it("is its own widget that wears the shared frame look, not the html widget's component", () => {
    const html = render(<HtmlWidget html="<p>x</p>" title="t" />).container;
    const browser = render(<BrowserWidget status="live" url="https://example.com/" frame={FRAME} />).container;
    for (const selector of ["section.osmo-frame", "header.osmo-frame-bar", ".osmo-frame-title", ".osmo-frame-tools", ".osmo-frame-viewport", ".osmo-frame-readout", "button.osmo-icon-button"]) {
      expect(html.querySelector(selector), `html ${selector}`).not.toBeNull();
      expect(browser.querySelector(selector), `browser ${selector}`).not.toBeNull();
    }
    expect(browser.querySelector("iframe")).toBeNull();
    expect(html.querySelector(".osmo-browser")).toBeNull();
  });

  it("shows the address in the title, a live readout, and the latest frame", () => {
    render(<BrowserWidget status="live" url="https://example.com/" frame={FRAME} />);
    expect(screen.getByText("Agent browser · https://example.com/")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Live");
    expect(document.querySelector("img")?.getAttribute("src")).toBe(FRAME);
  });

  it("says it is starting until the first frame, and ended with the last frame kept", () => {
    const { rerender } = render(<BrowserWidget status="live" />);
    expect(screen.getByText("Starting the browser…")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    rerender(<BrowserWidget status="ended" url="https://example.com/" frame={FRAME} />);
    expect(screen.getByRole("status").textContent).toBe("Ended");
    expect(document.querySelector("img")?.getAttribute("src")).toBe(FRAME);
  });

  it("opens fullscreen like the html widget, keeps streaming there, and closes", () => {
    const { rerender } = render(<BrowserWidget status="live" url="https://example.com/" frame={FRAME} />);
    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelectorAll("img")).toHaveLength(1);
    expect(document.querySelectorAll("img")).toHaveLength(1);
    const next = "data:image/jpeg;base64,/9j/4BBB";
    rerender(<BrowserWidget status="live" url="https://example.com/" frame={next} />);
    expect(screen.getByRole("dialog").querySelector("img")?.getAttribute("src")).toBe(next);
    fireEvent.click(screen.getByRole("button", { name: "Close fullscreen" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector("img")?.getAttribute("src")).toBe(next);
  });
});
