import type { Thread } from "@osmo/osmo-composer";
import { relativeTime } from "@/lib/threads";

export interface RecentThreadsProps {
  threads: readonly Thread[];
  now: number;
  onResume: (id: string) => void;
  onViewAll: () => void;
}

const RECENT_COUNT = 5;

/** The latest threads; hidden until the first one exists. */
export function RecentThreads({ threads, now, onResume, onViewAll }: RecentThreadsProps) {
  if (threads.length === 0) return null;
  const recent = [...threads].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, RECENT_COUNT);
  return (
    <section className="osmo-section" aria-label="Recent">
      <h2 className="osmo-section-label">
        <span>Recent</span>
        <button type="button" className="osmo-link" onClick={onViewAll}>
          View all
        </button>
      </h2>
      {recent.map((thread) => (
        <button key={thread.id} type="button" className="osmo-list-row osmo-recent-row" onClick={() => onResume(thread.id)}>
          <span className="osmo-list-title">{thread.title}</span>
          <span className="osmo-tertiary">{relativeTime(thread.updatedAt, now)}</span>
          <span className="osmo-muted osmo-resume">Resume →</span>
        </button>
      ))}
    </section>
  );
}
