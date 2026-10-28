import { type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CloseIcon, ExpandIcon, INLINE_HEIGHT, useFullscreen } from "./frame";
import type { BrowserProps } from "./schema";

/**
 * The agent browser: the latest frame of the browser a background run is driving. It is its own widget
 * (own type, props and lifecycle) that wears the shared `.osmo-frame*` look of the html widget. The title
 * carries the page address and a readout says whether the run still uses the browser. The server streams
 * CDP screencast frames into `frame`; when the run ends `status` becomes "ended" and the last frame stays.
 */
export function BrowserWidget({ status, url, frame }: BrowserProps) {
  const [fullscreen, setFullscreen] = useFullscreen();
  const title = url ? `Agent browser · ${url}` : "Agent browser";

  const header = (trailing: ReactNode) => (
    <header className="osmo-frame-bar">
      <span className="osmo-frame-title">{title}</span>
      <div className="osmo-frame-tools" role="toolbar" aria-label={`${title} controls`}>
        <span className="osmo-frame-readout" role="status">
          {status === "live" ? "Live" : "Ended"}
        </span>
        {trailing}
      </div>
    </header>
  );
  const viewport = (height: number | undefined) => (
    <div className="osmo-frame-viewport" style={height === undefined ? undefined : { height }}>
      {frame ? (
        <img src={frame} alt={url ? `The agent browser showing ${url}` : "The agent browser"} />
      ) : (
        <p className="osmo-browser-wait">Starting the browser…</p>
      )}
    </div>
  );

  return (
    <section className="osmo-frame osmo-browser" aria-label="Agent browser" data-status={status}>
      {header(
        <button type="button" className="osmo-icon-button" aria-label="Open fullscreen" title="Fullscreen" onClick={() => setFullscreen(true)}>
          <ExpandIcon />
        </button>,
      )}
      {!fullscreen && viewport(INLINE_HEIGHT)}
      {fullscreen &&
        createPortal(
          <div className="osmo-frame-overlay" onClick={() => setFullscreen(false)}>
            <div className="osmo-frame-modal osmo-browser" role="dialog" aria-modal="true" aria-label="Agent browser" onClick={(event) => event.stopPropagation()}>
              {header(
                <button type="button" className="osmo-icon-button" aria-label="Close fullscreen" title="Close (Esc)" onClick={() => setFullscreen(false)} autoFocus>
                  <CloseIcon />
                </button>,
              )}
              {viewport(undefined)}
            </div>
          </div>,
          document.body,
        )}
    </section>
  );
}
