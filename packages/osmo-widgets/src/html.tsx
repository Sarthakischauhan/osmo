import { type ReactNode, useState } from "react";
import { createPortal } from "react-dom";
import { CloseIcon, ExpandIcon, INLINE_HEIGHT, Icon, useFullscreen } from "./frame";
import type { HtmlProps } from "./schema";

/**
 * Generated HTML in an opaque-origin iframe (scripts yes, same-origin no,
 * network no) under a header bar: title, reload, zoom and fullscreen.
 * chat-sdk's ArtifactWidget previews files as text/images only, so this stays local.
 */

export const ZOOM_LEVELS = [0.75, 1, 1.25, 1.5] as const;
const DEFAULT_ZOOM = 1;
const CSP = `<meta http-equiv="Content-Security-Policy" content="connect-src 'none'">`;

/** Puts the CSP meta inside <head> (or right after the doctype) so the document keeps standards mode. */
export function withCsp(html: string): string {
  const head = /<head[^>]*>/i.exec(html);
  if (head) return html.slice(0, head.index + head[0].length) + CSP + html.slice(head.index + head[0].length);
  const doctype = /^\s*<!doctype[^>]*>/i.exec(html);
  if (doctype) return doctype[0] + CSP + html.slice(doctype[0].length);
  return CSP + html;
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
  html: string;
  title: string;
  state: FrameState;
  height: number | undefined;
}

function HtmlFrame({ html, title, state, height }: HtmlBodyProps) {
  const scale = state.zoom;
  return (
    <div className="osmo-frame-viewport" style={height === undefined ? undefined : { height }}>
      <iframe
        key={state.generation}
        title={title}
        sandbox="allow-scripts allow-forms"
        srcDoc={withCsp(html)}
        referrerPolicy="no-referrer"
        data-zoom={scale}
        style={{ width: `${100 / scale}%`, height: `${100 / scale}%`, transform: `scale(${scale})` }}
      />
    </div>
  );
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

export function HtmlWidget({ html, title = "Widget" }: HtmlProps) {
  const [state, setState] = useState<FrameState>({ zoom: DEFAULT_ZOOM, generation: 0 });
  const [fullscreen, setFullscreen] = useFullscreen();

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
      {!fullscreen && <HtmlFrame html={html} title={title} state={state} height={INLINE_HEIGHT} />}
      {fullscreen &&
        createPortal(
          <div className="osmo-frame-overlay" onClick={() => setFullscreen(false)}>
            <div className="osmo-frame-modal" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
              {header(
                <button type="button" className="osmo-icon-button" aria-label="Close fullscreen" title="Close (Esc)" onClick={() => setFullscreen(false)} autoFocus>
                  <CloseIcon />
                </button>,
              )}
              <HtmlFrame html={html} title={title} state={state} height={undefined} />
            </div>
          </div>,
          document.body,
        )}
    </section>
  );
}
