import type { BrowserProps } from "./schema";

/**
 * The agent browser: the latest frame of the browser a background run is driving, under a header
 * with the page address and whether the run is still using it. The server streams CDP screencast
 * frames into `frame`; when the run ends `status` becomes "ended" and the last frame stays.
 */
export function BrowserWidget({ status, url, frame }: BrowserProps) {
  const live = status === "live";
  return (
    <section className="osmo-browser" aria-label="Agent browser" data-status={status}>
      <header className="osmo-html-bar">
        <span className="osmo-html-title">Agent browser</span>
        <span className="osmo-browser-url" title={url}>
          {url}
        </span>
        <span className="osmo-browser-status" role="status">
          <span className="osmo-browser-dot" aria-hidden="true" />
          {live ? "Live" : "Ended"}
        </span>
      </header>
      <div className="osmo-browser-viewport">
        {frame ? <img src={frame} alt={url ? `The agent browser showing ${url}` : "The agent browser"} /> : <p className="osmo-browser-wait">Starting the browser…</p>}
      </div>
    </section>
  );
}
