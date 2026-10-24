// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { HtmlWidget, withCsp } from "../src/html";

afterEach(cleanup);

const DOC = "<!doctype html><html><head><title>t</title></head><body><button>tip</button></body></html>";

function frames(): HTMLIFrameElement[] {
  return Array.from(document.querySelectorAll("iframe"));
}

function onlyFrame(): HTMLIFrameElement {
  const [frame, ...rest] = frames();
  if (!frame || rest.length) throw new Error(`expected one iframe, found ${frames().length}`);
  return frame;
}

describe("HtmlWidget", () => {
  it("renders a header with the title and labelled icon buttons over a locked-down iframe", () => {
    render(<HtmlWidget html={DOC} title="Tip calculator" />);
    expect(screen.getByText("Tip calculator")).toBeTruthy();
    for (const label of ["Reload", "Zoom in", "Zoom out", "Open fullscreen"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button.querySelector("svg")).not.toBeNull();
    }
    const frame = onlyFrame();
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-forms");
    expect(frame.getAttribute("srcdoc")).toContain(`connect-src 'none'`);
    expect(frame.getAttribute("title")).toBe("Tip calculator");
  });

  it("falls back to a generic title", () => {
    render(<HtmlWidget html="<p>x</p>" />);
    expect(screen.getByText("Widget")).toBeTruthy();
  });

  it("reload re-mounts the iframe", () => {
    render(<HtmlWidget html={DOC} title="t" />);
    const before = onlyFrame();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    const after = onlyFrame();
    expect(after).not.toBe(before);
    expect(before.isConnected).toBe(false);
  });

  it("zooms through levels and clamps at the ends", () => {
    render(<HtmlWidget html={DOC} title="t" />);
    const zoomIn = screen.getByRole("button", { name: "Zoom in" });
    const zoomOut = screen.getByRole("button", { name: "Zoom out" });
    expect(screen.getByText("100%")).toBeTruthy();
    fireEvent.click(zoomIn);
    expect(onlyFrame().getAttribute("data-zoom")).toBe("1.25");
    expect(onlyFrame().style.transform).toBe("scale(1.25)");
    fireEvent.click(zoomIn);
    expect(screen.getByText("150%")).toBeTruthy();
    expect(zoomIn.hasAttribute("disabled")).toBe(true);
    fireEvent.click(zoomOut);
    fireEvent.click(zoomOut);
    fireEvent.click(zoomOut);
    expect(screen.getByText("75%")).toBeTruthy();
    expect(zoomOut.hasAttribute("disabled")).toBe(true);
  });

  it("opens fullscreen in a modal with the same bar, and the close button closes it", () => {
    render(<HtmlWidget html={DOC} title="Tip calculator" />);
    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    const dialog = screen.getByRole("dialog", { name: "Tip calculator" });
    expect(dialog.parentElement?.parentElement).toBe(document.body);
    expect(dialog.querySelector("iframe")).not.toBeNull();
    expect(onlyFrame().getAttribute("sandbox")).toBe("allow-scripts allow-forms");
    for (const label of ["Reload", "Zoom in", "Zoom out"]) {
      expect(dialog.querySelector(`button[aria-label="${label}"]`)).not.toBeNull();
    }
    fireEvent.click(screen.getByRole("button", { name: "Close fullscreen" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onlyFrame().closest('[role="dialog"]')).toBeNull();
  });

  it("Esc closes the modal", () => {
    render(<HtmlWidget html={DOC} title="t" />);
    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("withCsp", () => {
  it("injects the CSP inside head, after a doctype, or at the start", () => {
    expect(withCsp("<html><head></head></html>")).toMatch(/^<html><head><meta http-equiv="Content-Security-Policy"/);
    expect(withCsp("<!doctype html><p>x</p>")).toMatch(/^<!doctype html><meta/);
    expect(withCsp("<p>x</p>")).toMatch(/^<meta/);
  });
});
