import { type RefObject, useCallback, useEffect, useState } from "react";

export interface ScrollDirectionMetrics {
  scrollable: boolean;
  canUp: boolean;
  canDown: boolean;
  ratio: number;
}

const EDGE_PX = 4;
const STEP_FRACTION = 0.65;

export function computeScrollMetrics(scrollTop: number, scrollHeight: number, clientHeight: number): ScrollDirectionMetrics {
  const max = scrollHeight - clientHeight;
  if (max <= EDGE_PX || clientHeight <= 0) {
    return { scrollable: false, canUp: false, canDown: false, ratio: 0.5 };
  }
  const clamped = Math.min(max, Math.max(0, scrollTop));
  return {
    scrollable: true,
    canUp: clamped > EDGE_PX,
    canDown: clamped < max - EDGE_PX,
    ratio: max > 0 ? clamped / max : 0.5,
  };
}

export interface ScrollDirectionBarProps {
  target: RefObject<HTMLElement | null>;
  label?: string;
}

/**
 * Minimal scrollbar direction indicator for `.osmo-scroll` containers (whose native bar is hidden):
 * small up/down direction chevrons at the ends, and a middle marker showing the current/selected scroll position.
 */
export function ScrollDirectionBar({ target, label = "Scroll position" }: ScrollDirectionBarProps) {
  const [metrics, setMetrics] = useState<ScrollDirectionMetrics>({
    scrollable: false,
    canUp: false,
    canDown: false,
    ratio: 0.5,
  });

  const sync = useCallback(() => {
    const el = target.current;
    if (!el) return;
    setMetrics(computeScrollMetrics(el.scrollTop, el.scrollHeight, el.clientHeight));
  }, [target]);

  useEffect(() => {
    const el = target.current;
    if (!el) return;
    sync();
    const onScroll = () => sync();
    el.addEventListener("scroll", onScroll, { passive: true });
    const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(sync) : undefined;
    resize?.observe(el);
    const firstChild = el.firstElementChild;
    if (firstChild) resize?.observe(firstChild);
    const mutation = typeof MutationObserver !== "undefined" ? new MutationObserver(sync) : undefined;
    mutation?.observe(el, { childList: true, subtree: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      resize?.disconnect();
      mutation?.disconnect();
    };
  }, [target, sync]);

  const step = (direction: -1 | 1) => {
    const el = target.current;
    if (!el) return;
    el.scrollBy({ top: direction * Math.max(80, Math.round(el.clientHeight * STEP_FRACTION)), behavior: "smooth" });
  };

  const center = () => {
    const el = target.current;
    if (!el) return;
    const selected = el.querySelector<HTMLElement>('[data-active="true"], [aria-selected="true"]');
    if (selected) {
      selected.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    el.scrollTo({ top: Math.round(max / 2), behavior: "smooth" });
  };

  if (!metrics.scrollable) return null;

  return (
    <div className="osmo-scroll-indicator" role="group" aria-label={label}>
      <button
        type="button"
        className="osmo-scroll-dir"
        aria-label="Scroll up"
        disabled={!metrics.canUp}
        onClick={() => step(-1)}
      >
        <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2.5 7.5 6 4l3.5 3.5" />
        </svg>
      </button>
      <button
        type="button"
        className="osmo-scroll-track"
        aria-label="Center selected position"
        title="Selected position"
        onClick={center}
      >
        <span className="osmo-scroll-mid" aria-hidden="true" />
        <span
          className="osmo-scroll-thumb"
          data-centered={Math.abs(metrics.ratio - 0.5) <= 0.12 || undefined}
          style={{ top: `${Math.round(metrics.ratio * 100)}%` }}
          aria-hidden="true"
        />
      </button>
      <button
        type="button"
        className="osmo-scroll-dir"
        aria-label="Scroll down"
        disabled={!metrics.canDown}
        onClick={() => step(1)}
      >
        <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" />
        </svg>
      </button>
    </div>
  );
}
