import { useEffect, useState } from "react";
import { BAND_LABEL, FRAMES, getBand, type Band } from "@/lib/asciiFrames";

const STAR_BANDS: ReadonlySet<Band> = new Set<Band>(["evening", "night"]);
const SWAP: Record<string, string> = { ".": "*", "*": ".", "+": "." };

/** Swap 1-2 random star characters (`.`, `*`, `+`) in a frame. */
export function twinkle(frame: string, rand: () => number = Math.random): string {
  const stars: number[] = [];
  for (let i = 0; i < frame.length; i++) {
    const ch = frame[i];
    // Only isolated star chars, so the moon's shading never changes.
    if (ch && ch in SWAP && frame[i - 1] !== "#" && frame[i + 1] !== "#" && (frame[i - 1] ?? " ").trim() === "" && (frame[i + 1] ?? " ").trim() === "") stars.push(i);
  }
  if (stars.length === 0) return frame;
  const chars = frame.split("");
  const count = 1 + Math.floor(rand() * 2);
  for (let n = 0; n < count && stars.length > 0; n++) {
    const [at] = stars.splice(Math.floor(rand() * stars.length), 1);
    if (at === undefined) continue;
    const ch = chars[at];
    if (ch !== undefined) chars[at] = SWAP[ch] ?? ch;
  }
  return chars.join("");
}

function useBand(): Band {
  const [band, setBand] = useState<Band>(() => getBand());
  useEffect(() => {
    const update = () => setBand(getBand());
    const timer = window.setInterval(update, 60_000);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return band;
}

function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** The empty-thread screen: one time-of-day ASCII frame above the orb (GUI spec). */
export function AsciiHome() {
  const band = useBand();
  const reduced = useReducedMotion();
  const [shown, setShown] = useState<Band>(band);
  const [frame, setFrame] = useState(FRAMES[band]);
  const [fading, setFading] = useState(false);

  // Band change: 200ms opacity fade out, swap, fade in.
  useEffect(() => {
    if (band === shown) return;
    setFading(true);
    const t = window.setTimeout(() => {
      setShown(band);
      setFrame(FRAMES[band]);
      setFading(false);
    }, 200);
    return () => window.clearTimeout(t);
  }, [band, shown]);

  // Twinkle: star bands only, every 2-4s, off under reduced motion.
  useEffect(() => {
    setFrame(FRAMES[shown]);
    if (reduced || !STAR_BANDS.has(shown)) return;
    let t = 0;
    const tick = () => {
      t = window.setTimeout(() => {
        setFrame(twinkle(FRAMES[shown]));
        tick();
      }, 2000 + Math.random() * 2000);
    };
    tick();
    return () => window.clearTimeout(t);
  }, [shown, reduced]);

  return (
    <div className="osmo-home osmo-ascii-home" data-band={shown}>
      <pre aria-hidden="true" className="osmo-ascii" data-fading={fading}>
        {frame}
      </pre>
      <span className="sr-only">{BAND_LABEL[shown]}</span>
    </div>
  );
}
