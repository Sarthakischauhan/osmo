import { useEffect, useState } from "react";

/** The current time in ms, updated every second. */
export function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

/** How long the call has been going, as mm:ss. */
export function Clock({ since }: { since: number }) {
  const now = useNow();
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return <span className="clock">{`${mm}:${ss}`}</span>;
}
