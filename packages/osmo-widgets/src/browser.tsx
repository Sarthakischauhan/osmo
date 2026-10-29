import { type KeyboardEvent, type MouseEvent, type ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { BrowserInput } from "./controls";
import { CloseIcon, ExpandIcon, INLINE_HEIGHT, PowerIcon, useFullscreen } from "./frame";
import type { BrowserProps } from "./schema";

interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Where a pointer is on the page a frame shows, as fractions (0 to 1) across and down it; undefined when it is
 * outside the page. The frame is drawn `object-fit: contain` in `box`, so the page is the largest centred
 * rectangle of the frame's aspect ratio that fits there, and the bars around it are not the page.
 */
export function pagePoint(box: Box, frame: { width: number; height: number }, clientX: number, clientY: number): { x: number; y: number } | undefined {
  if (frame.width === 0 || frame.height === 0) return undefined;
  const scale = Math.min(box.width / frame.width, box.height / frame.height);
  const width = frame.width * scale;
  const height = frame.height * scale;
  const x = (clientX - box.left - (box.width - width) / 2) / width;
  const y = (clientY - box.top - (box.height - height) / 2) / height;
  return x < 0 || x > 1 || y < 0 || y > 1 ? undefined : { x, y };
}

function pointOn(image: HTMLImageElement | null, clientX: number, clientY: number) {
  return image ? pagePoint(image.getBoundingClientRect(), { width: image.naturalWidth, height: image.naturalHeight }, clientX, clientY) : undefined;
}

/**
 * The agent browser: the latest frame of the browser a background run is driving. It is its own widget
 * (own type, props and lifecycle) that wears the shared `.osmo-frame*` look of the html widget. The title
 * carries the page address and a readout says whether the browser is still up. The server streams CDP
 * screencast frames into `frame`; when the browser is closed `status` becomes "ended" and the last frame stays.
 *
 * Fullscreen of a live browser is also the user's hands on it: clicks, scrolls and keys on the page go to
 * `onInput` (the app sends them to the server, which plays them into the page; no approval, they are the user's own).
 */
export function BrowserWidget({ status, url, frame, onInput, onClose }: BrowserProps & { onInput?: (input: BrowserInput) => void; onClose?: () => void }) {
  const [fullscreen, setFullscreen] = useFullscreen();
  const title = url ? `Agent browser · ${url}` : "Agent browser";
  const driving = fullscreen && status === "live" && onInput !== undefined;
  const page = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);

  // A wheel handler must be able to stop the page behind the modal from scrolling, which React's passive one cannot.
  useEffect(() => {
    const element = page.current;
    if (!driving || !element) return;
    const onWheel = (event: WheelEvent) => {
      const at = pointOn(image.current, event.clientX, event.clientY);
      if (!at) return;
      event.preventDefault();
      onInput({ type: "browser.scroll", ...at, deltaX: event.deltaX, deltaY: event.deltaY });
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [driving, onInput]);

  const click = (event: MouseEvent) => {
    const at = pointOn(image.current, event.clientX, event.clientY);
    if (at && onInput) onInput({ type: "browser.click", ...at });
  };
  const key = (event: KeyboardEvent) => {
    // Escape leaves fullscreen and shortcuts stay the browser's own; everything else is typed into the page.
    if (!onInput || event.key === "Escape" || event.ctrlKey || event.metaKey || event.altKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    onInput({ type: "browser.key", key: event.key, code: event.code, keyCode: event.keyCode });
  };

  const header = (trailing: ReactNode) => (
    <header className="osmo-frame-bar">
      <span className="osmo-frame-title">{title}</span>
      <div className="osmo-frame-tools" role="toolbar" aria-label={`${title} controls`}>
        <span className="osmo-frame-readout" role="status">
          {status === "live" ? "Live" : "Ended"}
        </span>
        {status === "live" && onClose && (
          <button type="button" className="osmo-icon-button" aria-label="Close browser" title="Close browser" onClick={onClose}>
            <PowerIcon />
          </button>
        )}
        {trailing}
      </div>
    </header>
  );
  const viewport = (height: number | undefined) => (
    <div
      ref={page}
      className={driving ? "osmo-frame-viewport osmo-browser-live" : "osmo-frame-viewport"}
      style={height === undefined ? undefined : { height }}
      role={driving ? "application" : undefined}
      tabIndex={driving ? 0 : undefined}
      aria-label={driving ? "The page: click, scroll or type to use it" : undefined}
      onClick={driving ? click : undefined}
      onKeyDown={driving ? key : undefined}
    >
      {frame ? (
        <img ref={image} src={frame} alt={url ? `The agent browser showing ${url}` : "The agent browser"} draggable={false} />
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
