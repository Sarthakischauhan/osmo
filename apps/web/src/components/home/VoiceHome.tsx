import type { ReactNode } from "react";

export interface VoiceHomeProps {
  /** The user's first name, when known. */
  name: string | undefined;
  now: number;
  pill: ReactNode;
  runs: ReactNode;
  recent: ReactNode;
}

export function greeting(now: number, name: string | undefined): string {
  const hour = new Date(now).getHours();
  const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}` : part;
}

/** The empty-thread screen: greeting, provider pill, background runs and recent threads. */
export function VoiceHome({ name, now, pill, runs, recent }: VoiceHomeProps) {
  return (
    <div className="osmo-home">
      <div className="osmo-greeting-row">
        <div>
          <h1 className="osmo-greeting">{greeting(now, name)}</h1>
          <p className="osmo-muted">Tap the orb or press Space to talk.</p>
        </div>
        {pill}
      </div>
      {runs}
      {recent}
    </div>
  );
}
