// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { HtmlWidget, withKit } from "../src/html";
import { OSMO_UI_CSS } from "../src/kit/osmoUi";

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
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    for (const directive of ["default-src 'none'", "connect-src 'none'", "img-src data: blob:", "form-action 'none'", "base-uri 'none'"]) {
      expect(srcdoc).toContain(directive);
    }
    expect(srcdoc).toContain("window.osmo");
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

describe("withKit", () => {
  it("puts the CSP, kit and bridge first: right after a doctype, or at the start", () => {
    expect(withKit("<html><head></head></html>", "dark")).toMatch(/^<meta http-equiv="Content-Security-Policy"/);
    expect(withKit("<!doctype html><p>x</p>", "dark")).toMatch(/^<!doctype html><meta/);
    expect(withKit("<p>x</p>", "dark")).toMatch(/^<meta/);
    const kit = withKit("<p>x</p>", "light");
    expect(kit.indexOf("<style>")).toBeLessThan(kit.indexOf("<p>x</p>"));
    expect(kit.indexOf("<script>")).toBeLessThan(kit.indexOf("<p>x</p>"));
    expect(kit).toContain('dataset.theme = "light"');
  });

  // Review B1: a CSP meta outside <head> is ignored, and a script parsed before the meta runs unrestricted.
  const leak = '<script>fetch("http://127.0.0.1:8799/leak")</script>';
  it.each([
    ["a <header> and no <head>", `<!doctype html><body><header>Today</header>${leak}</body>`],
    ["a script before <head>", `${leak}<html><head></head><body>hi</body></html>`],
    ["an ordinary document", `<!doctype html><html><head><title>x</title></head><body>${leak}</body></html>`],
  ])("puts the CSP in the head before any widget script, for %s", (_case, html) => {
    const doc = new DOMParser().parseFromString(withKit(html, "dark"), "text/html");
    const csp = doc.querySelector('meta[http-equiv="Content-Security-Policy"]');
    expect(csp?.parentElement).toBe(doc.head);
    expect(doc.head.firstElementChild).toBe(csp);
    for (const script of Array.from(doc.querySelectorAll("script"))) {
      expect(csp?.compareDocumentPosition(script) ?? 0).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    }
  });

  it("defines every class the render_widget description tells the model about", () => {
    const widgets = readFileSync(join(__dirname, "../../../apps/symphony-server/src/symphony_server/widgets.py"), "utf8");
    const listed = /instead of inline CSS: "\s*"([^"]+)"\s*"([^"]+)"/.exec(widgets);
    expect(listed).not.toBeNull();
    const names = `${listed?.[1]} ${listed?.[2]}`.replace(/\([^)]*\)/g, "").split(/[,.\s]+/).filter(Boolean);
    expect(names.length).toBeGreaterThan(14);
    for (const name of names) expect(OSMO_UI_CSS).toMatch(new RegExp(`\\.${name}\\b`));
  });

  it("uses 8px radius buttons", () => {
    expect(OSMO_UI_CSS).toContain("--radius-md: 8px");
    expect(/\.button \{[^}]*border-radius: var\(--radius-md\)/.exec(OSMO_UI_CSS)).not.toBeNull();
  });
});

describe("click bridge", () => {
  const actions = [{ id: "approve", label: "Approve" }];
  const post = (source: MessageEventSource | null, data: unknown) =>
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data, source }));
    });
  // jsdom has no user activation: each test says whether the user just clicked.
  const activation = (isActive: boolean) => Object.defineProperty(navigator, "userActivation", { value: { isActive, hasBeenActive: isActive }, configurable: true });
  beforeEach(() => activation(true));
  afterEach(() => activation(false));

  it("reports a declared action from its own frame, with the value", () => {
    const onAction = vi.fn();
    render(<HtmlWidget html={DOC} title="t" actions={actions} onAction={onAction} />);
    post(onlyFrame().contentWindow, { osmo: "action", id: "approve", value: "yes" });
    expect(onAction).toHaveBeenCalledWith({ id: "approve", label: "Approve" }, "yes");
  });

  it("drops undeclared ids, malformed messages and messages from any other window", () => {
    const onAction = vi.fn();
    render(<HtmlWidget html={DOC} title="t" actions={actions} onAction={onAction} />);
    const own = onlyFrame().contentWindow;
    post(own, { osmo: "action", id: "delete" });
    post(own, { osmo: "action", id: "approve", extra: 1 });
    post(own, { osmo: "action", id: "approve", value: "x".repeat(2001) });
    post(window, { osmo: "action", id: "approve" });
    post(null, { osmo: "action", id: "approve" });
    expect(onAction).not.toHaveBeenCalled();
  });

  // Review S4: a widget script must not answer for the user, and a widget answers once.
  it("ignores a message the frame posts without a user click", () => {
    const onAction = vi.fn();
    render(<HtmlWidget html={DOC} title="t" actions={actions} onAction={onAction} />);
    activation(false);
    post(onlyFrame().contentWindow, { osmo: "action", id: "approve" });
    expect(onAction).not.toHaveBeenCalled();
  });

  it("takes one answer per widget", () => {
    const onAction = vi.fn();
    render(<HtmlWidget html={DOC} title="t" actions={[...actions, { id: "hold", label: "Hold" }]} onAction={onAction} />);
    post(onlyFrame().contentWindow, { osmo: "action", id: "approve" });
    post(onlyFrame().contentWindow, { osmo: "action", id: "hold" });
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("follows the frame into fullscreen", () => {
    const onAction = vi.fn();
    render(<HtmlWidget html={DOC} title="t" actions={actions} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Open fullscreen" }));
    post(onlyFrame().contentWindow, { osmo: "action", id: "approve" });
    expect(onAction).toHaveBeenCalledWith({ id: "approve", label: "Approve" }, undefined);
  });
});
