// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserWidget, pagePoint } from "../src/browser";
import type { BrowserInput } from "../src/controls";
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

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return { left, top, width, height, x: left, y: top, right: left + width, bottom: top + height, toJSON: () => ({}) };
}

describe("pagePoint", () => {
  const frame = { width: 800, height: 600 };

  it("maps a point of a frame that fills its box to fractions of the page", () => {
    expect(pagePoint(rect(10, 20, 400, 300), frame, 10, 20)).toEqual({ x: 0, y: 0 });
    expect(pagePoint(rect(10, 20, 400, 300), frame, 210, 170)).toEqual({ x: 0.5, y: 0.5 });
    expect(pagePoint(rect(10, 20, 400, 300), frame, 110, 245)).toEqual({ x: 0.25, y: 0.75 });
  });

  it("leaves out the bars object-fit: contain leaves beside a frame that is taller than its box", () => {
    // The page is drawn 400 wide in the middle of an 800 x 300 box: 200 of bar on each side.
    const box = rect(0, 0, 800, 300);
    expect(pagePoint(box, frame, 400, 150)).toEqual({ x: 0.5, y: 0.5 });
    expect(pagePoint(box, frame, 200, 0)).toEqual({ x: 0, y: 0 });
    expect(pagePoint(box, frame, 100, 150)).toBeUndefined();
    expect(pagePoint(box, frame, 700, 150)).toBeUndefined();
  });

  it("leaves out the bars above and below a frame that is wider than its box", () => {
    // The page is drawn 400 x 300 in a 400 x 500 box: 100 of bar above and below.
    const box = rect(0, 0, 400, 500);
    expect(pagePoint(box, frame, 200, 250)).toEqual({ x: 0.5, y: 0.5 });
    expect(pagePoint(box, frame, 200, 50)).toBeUndefined();
    expect(pagePoint(box, frame, 200, 450)).toBeUndefined();
  });

  it("knows no page before the frame has loaded", () => {
    expect(pagePoint(rect(0, 0, 400, 300), { width: 0, height: 0 }, 10, 10)).toBeUndefined();
  });
});

describe("BrowserWidget in fullscreen", () => {
  /** Opens fullscreen and makes the frame 800 x 600 shown in a 400 x 400 box at (100, 50): the page is 400 x 300, 50 of bar above and below. */
  function open(props: { status: "live" | "ended" }, onInput?: (input: BrowserInput) => void, onClose?: () => void) {
    render(<BrowserWidget {...props} url="https://example.com/" frame={FRAME} onInput={onInput} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    const image = screen.getByRole("dialog").querySelector("img");
    if (!image) throw new Error("no frame");
    Object.defineProperty(image, "naturalWidth", { value: 800 });
    Object.defineProperty(image, "naturalHeight", { value: 600 });
    image.getBoundingClientRect = () => rect(100, 50, 400, 400);
    return screen.getByRole("dialog").querySelector<HTMLElement>(".osmo-frame-viewport");
  }

  it("sends the user's click, as fractions of the page", () => {
    const onInput = vi.fn();
    const page = open({ status: "live" }, onInput);
    if (!page) throw new Error("no page");
    fireEvent.click(page, { clientX: 200, clientY: 250 });
    expect(onInput).toHaveBeenCalledWith({ type: "browser.click", x: 0.25, y: 0.5 });
    // On a bar, not the page: nothing happens.
    fireEvent.click(page, { clientX: 200, clientY: 60 });
    expect(onInput).toHaveBeenCalledTimes(1);
  });

  it("sends a scroll where the pointer is and keeps the page behind from scrolling", () => {
    const onInput = vi.fn();
    const page = open({ status: "live" }, onInput);
    if (!page) throw new Error("no page");
    const notPrevented = fireEvent.wheel(page, { clientX: 300, clientY: 250, deltaX: 3, deltaY: 120 });
    expect(notPrevented).toBe(false);
    expect(onInput).toHaveBeenCalledWith({ type: "browser.scroll", x: 0.5, y: 0.5, deltaX: 3, deltaY: 120 });
    // Over a bar the wheel is the browser's own.
    expect(fireEvent.wheel(page, { clientX: 300, clientY: 450, deltaY: 120 })).toBe(true);
    expect(onInput).toHaveBeenCalledTimes(1);
  });

  it("sends keys, including Enter, and leaves Escape and shortcuts alone", () => {
    const onInput = vi.fn();
    const page = open({ status: "live" }, onInput);
    if (!page) throw new Error("no page");
    expect(fireEvent.keyDown(page, { key: "a", code: "KeyA", keyCode: 65 })).toBe(false);
    expect(fireEvent.keyDown(page, { key: "Enter", code: "Enter", keyCode: 13 })).toBe(false);
    expect(onInput.mock.calls).toEqual([
      [{ type: "browser.key", key: "a", code: "KeyA", keyCode: 65 }],
      [{ type: "browser.key", key: "Enter", code: "Enter", keyCode: 13 }],
    ]);
    // A shortcut is not typed into the page, and the browser keeps it.
    expect(fireEvent.keyDown(page, { key: "r", code: "KeyR", keyCode: 82, metaKey: true })).toBe(true);
    expect(onInput).toHaveBeenCalledTimes(2);
    // Escape leaves fullscreen.
    fireEvent.keyDown(page, { key: "Escape", code: "Escape", keyCode: 27 });
    expect(onInput).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("can be focused to type, and says so", () => {
    const page = open({ status: "live" }, vi.fn());
    expect(page?.getAttribute("tabindex")).toBe("0");
    expect(page?.getAttribute("role")).toBe("application");
  });

  it("is only a view when the browser has ended or nobody handles input", () => {
    const onInput = vi.fn();
    const ended = open({ status: "ended" }, onInput);
    if (!ended) throw new Error("no page");
    fireEvent.click(ended, { clientX: 200, clientY: 250 });
    fireEvent.keyDown(ended, { key: "a", code: "KeyA", keyCode: 65 });
    expect(fireEvent.wheel(ended, { clientX: 300, clientY: 250, deltaY: 120 })).toBe(true);
    expect(onInput).not.toHaveBeenCalled();
    expect(ended.getAttribute("tabindex")).toBeNull();
    cleanup();

    const viewOnly = open({ status: "live" });
    expect(viewOnly?.getAttribute("tabindex")).toBeNull();
  });

  it("does not take input inline, only in fullscreen", () => {
    render(<BrowserWidget status="live" url="https://example.com/" frame={FRAME} onInput={vi.fn()} />);
    expect(document.querySelector(".osmo-frame-viewport")?.getAttribute("tabindex")).toBeNull();
  });

  it("closes the browser from its button while it is live, and offers no button after", () => {
    const onClose = vi.fn();
    const { rerender } = render(<BrowserWidget status="live" frame={FRAME} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close browser" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(<BrowserWidget status="ended" frame={FRAME} onClose={onClose} />);
    expect(screen.queryByRole("button", { name: "Close browser" })).toBeNull();
  });
});
