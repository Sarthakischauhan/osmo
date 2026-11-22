import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CloseIcon, ExpandIcon, INLINE_HEIGHT, Icon, useFullscreen } from "./frame";
import { type KitTheme, bridgeMessageSchema, bridgeScript } from "./kit/bridge";
import { OSMO_UI_CSS } from "./kit/osmoUi";
import type { HtmlProps, WidgetAction } from "./schema";

/**
 * Generated HTML in an opaque-origin iframe (scripts yes, same-origin no,
 * network no) under a header bar: title, reload, zoom and fullscreen. The frame gets the
 * Osmo UI kit (kit/osmoUi.ts) and the click bridge (kit/bridge.ts).
 * chat-sdk's ArtifactWidget previews files as text/images only, so this stays local.
 */

export const ZOOM_LEVELS = [0.75, 1, 1.25, 1.5] as const;
const DEFAULT_ZOOM = 1;
/**
 * Nothing loads from outside the document: inline script and style, data: images, fonts and media.
 * Forms cannot submit anywhere (the bridge sends them), and <base> cannot move relative URLs.
 */
export const CSP_POLICY =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'";

/**
 * The CSP, kit and bridge as the very first thing in the document: right after the doctype (which keeps standards
 * mode), or at the start. Never after a searched-for `<head>`: that match can be a `<header>` in the body, where a CSP
 * meta is ignored, and a script before `<head>` would run before the policy exists. The parser puts these elements in
 * the implied head, so the document's own scripts find `osmo` already defined and run under the policy.
 */
export function withKit(html: string, theme: KitTheme): string {
  const kit = `<meta http-equiv="Content-Security-Policy" content="${CSP_POLICY}"><style>${OSMO_UI_CSS}</style><script>${bridgeScript(theme)}</script>`;
  const doctype = /^\s*<!doctype[^>]*>/i.exec(html);
  if (doctype) return doctype[0] + kit + html.slice(doctype[0].length);
  return kit + html;
}

/** The app's appearance (the `light` class on the root element, set by appearance.ts), kept current. */
function useDocumentTheme(): KitTheme {
  const read = (): KitTheme => (document.documentElement.classList.contains("light") ? "light" : "dark");
  const [theme, setTheme] = useState<KitTheme>(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

const ReloadIcon = () => (
  <Icon>
    <path d="M20 12a8 8 0 1 1-2.34-5.66" />
    <path d="M20 4v5h-5" />
  </Icon>
);
const ZoomInIcon = () => (
  <Icon>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M20 20l-4.4-4.4M11 8v6M8 11h6" />
  </Icon>
);
const ZoomOutIcon = () => (
  <Icon>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M20 20l-4.4-4.4M8 11h6" />
  </Icon>
);
interface FrameState {
  zoom: number;
  generation: number;
}

interface HtmlBodyProps {
  srcDoc: string;
  title: string;
  state: FrameState;
  height: number | undefined;
  frame: RefObject<HTMLIFrameElement>;
  theme: KitTheme;
}

function HtmlFrame({ srcDoc, title, state, height, frame, theme }: HtmlBodyProps) {
  const scale = state.zoom;
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ osmo: "theme", theme }, "*");
  }, [frame, theme]);
  return (
    <div className="osmo-frame-viewport" style={height === undefined ? undefined : { height }}>
      <iframe
        key={state.generation}
        ref={frame}
        title={title}
        sandbox="allow-scripts allow-forms"
        srcDoc={srcDoc}
        referrerPolicy="no-referrer"
        data-zoom={scale}
        style={{ width: `${100 / scale}%`, height: `${100 / scale}%`, transform: `scale(${scale})` }}
      />
    </div>
  );
}

/**
 * The frame the user last moved focus into with a real click, tap or Tab (trusted focus events on its element, or
 * this window blurring onto it), or null once focus leaves it. A frame script cannot fake these: its own events stay
 * in its document, and an opaque-origin frame cannot take focus without the user.
 */
function useUserFocusedFrame(frame: RefObject<HTMLIFrameElement | null>): RefObject<HTMLIFrameElement | null> {
  const focused = useRef<HTMLIFrameElement | null>(null);
  useEffect(() => {
    const enter = (event: Event) => {
      const own = frame.current;
      if (event.isTrusted && own !== null && document.activeElement === own) focused.current = own;
    };
    const leave = (event: Event) => {
      if (event.isTrusted && document.activeElement !== frame.current) focused.current = null;
    };
    document.addEventListener("focusin", enter, true);
    window.addEventListener("blur", enter);
    document.addEventListener("focusout", leave, true);
    window.addEventListener("focus", leave);
    return () => {
      document.removeEventListener("focusin", enter, true);
      window.removeEventListener("blur", enter);
      document.removeEventListener("focusout", leave, true);
      window.removeEventListener("focus", leave);
    };
  }, [frame]);
  return focused;
}

/**
 * Whether a message from the frame can be the user's answer: the user is interacting right now (transient activation)
 * and that interaction is inside this widget's own frame, which they focused themselves. Activation alone is not
 * enough: typing or clicking in Osmo itself activates this window too, and a widget's timer could post then.
 */
function userGesturedIn(own: HTMLIFrameElement | null, focused: HTMLIFrameElement | null): boolean {
  return navigator.userActivation?.isActive === true && own !== null && focused === own && document.activeElement === own;
}

interface HeaderProps {
  title: string;
  zoom: number;
  onReload: () => void;
  onZoom: (direction: 1 | -1) => void;
  trailing: ReactNode;
}

function HtmlHeader({ title, zoom, onReload, onZoom, trailing }: HeaderProps) {
  const index = ZOOM_LEVELS.findIndex((level) => level === zoom);
  return (
    <header className="osmo-frame-bar">
      <span className="osmo-frame-title">{title}</span>
      <div className="osmo-frame-tools" role="toolbar" aria-label={`${title} controls`}>
        <button type="button" className="osmo-icon-button" aria-label="Reload" title="Reload" onClick={onReload}>
          <ReloadIcon />
        </button>
        <button type="button" className="osmo-icon-button" aria-label="Zoom out" title="Zoom out" disabled={index <= 0} onClick={() => onZoom(-1)}>
          <ZoomOutIcon />
        </button>
        <span className="osmo-frame-readout" aria-live="polite">
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          className="osmo-icon-button"
          aria-label="Zoom in"
          title="Zoom in"
          disabled={index >= ZOOM_LEVELS.length - 1}
          onClick={() => onZoom(1)}
        >
          <ZoomInIcon />
        </button>
        {trailing}
      </div>
    </header>
  );
}

export interface HtmlWidgetProps extends HtmlProps {
  /** A declared action the frame asked for; absent when the widget cannot be answered (already answered, or not live). */
  onAction?: (action: WidgetAction, value: string | undefined) => void;
}

export function HtmlWidget({ html, title = "Widget", actions, onAction }: HtmlWidgetProps) {
  const [state, setState] = useState<FrameState>({ zoom: DEFAULT_ZOOM, generation: 0 });
  const [fullscreen, setFullscreen] = useFullscreen();
  const theme = useDocumentTheme();
  // The theme the document starts in; later changes are posted, so a theme switch does not reload the frame.
  const [startTheme] = useState(theme);
  const srcDoc = withKit(html, startTheme);
  const frame = useRef<HTMLIFrameElement>(null);

  // One answer per widget: after the first accepted action the frame cannot send another.
  const answered = useRef(false);
  const focused = useUserFocusedFrame(frame);

  useEffect(() => {
    if (onAction === undefined || !actions?.length) return;
    const listen = (event: MessageEvent) => {
      const source = frame.current?.contentWindow;
      if (!source || event.source !== source || answered.current) return;
      const parsed = bridgeMessageSchema.safeParse(event.data);
      const action = parsed.success ? actions.find((declared) => declared.id === parsed.data.id) : undefined;
      if (!parsed.success || !action) return;
      if (!userGesturedIn(frame.current, focused.current)) return;
      answered.current = true;
      onAction(action, parsed.data.value);
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, [actions, onAction]);

  const reload = () => setState((s) => ({ ...s, generation: s.generation + 1 }));
  const zoom = (direction: 1 | -1) =>
    setState((s) => {
      const index = ZOOM_LEVELS.findIndex((level) => level === s.zoom);
      const next = ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, Math.max(0, index + direction))];
      return next === undefined ? s : { ...s, zoom: next };
    });

  const header = (trailing: ReactNode) => (
    <HtmlHeader title={title} zoom={state.zoom} onReload={reload} onZoom={zoom} trailing={trailing} />
  );

  return (
    <section className="osmo-frame osmo-html" aria-label={title}>
      {header(
        <button type="button" className="osmo-icon-button" aria-label="Open fullscreen" title="Fullscreen" onClick={() => setFullscreen(true)}>
          <ExpandIcon />
        </button>,
      )}
      {!fullscreen && <HtmlFrame srcDoc={srcDoc} title={title} state={state} height={INLINE_HEIGHT} frame={frame} theme={theme} />}
      {fullscreen &&
        createPortal(
          <div className="osmo-frame-overlay" onClick={() => setFullscreen(false)}>
            <div className="osmo-frame-modal" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
              {header(
                <button type="button" className="osmo-icon-button" aria-label="Close fullscreen" title="Close (Esc)" onClick={() => setFullscreen(false)} autoFocus>
                  <CloseIcon />
                </button>,
              )}
              <HtmlFrame srcDoc={srcDoc} title={title} state={state} height={undefined} frame={frame} theme={theme} />
            </div>
          </div>,
          document.body,
        )}
    </section>
  );
}
