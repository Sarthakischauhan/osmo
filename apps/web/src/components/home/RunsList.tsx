import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { type RunView, elapsed } from "@/lib/runs";

export interface RunsListProps {
  runs: readonly RunView[];
  now: number;
  /** Go to the run's pending approval. */
  onReview: (run: RunView) => void;
}

/** Symphony background runs: hidden when there are none. */
export function RunsList({ runs, now, onReview }: RunsListProps) {
  const [open, setOpen] = useState<RunView>();
  if (runs.length === 0) return null;
  return (
    <section className="osmo-section" aria-label="Running">
      <h2 className="osmo-section-label">
        <span>Running</span>
        <span className="osmo-mono">{runs.length}</span>
      </h2>
      {runs.map((run) => (
        <RunRow key={run.runId} run={run} now={now} onOpen={() => setOpen(run)} onReview={() => onReview(run)} />
      ))}
      <Dialog open={open !== undefined} onOpenChange={(next) => !next && setOpen(undefined)}>
        <DialogContent className="osmo-run-dialog">
          <DialogHeader>
            <DialogTitle>Symphony · {open?.task}</DialogTitle>
            <DialogDescription>{open && statusText(open)}</DialogDescription>
          </DialogHeader>
          {open?.phase && <p className="osmo-mono osmo-muted">{open.phase}</p>}
          {open?.result && <p>{open.result}</p>}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function statusText(run: RunView): string {
  switch (run.status) {
    case "running":
      return run.steps > 0 ? `Step ${run.steps}` : "Starting";
    case "waiting_approval":
      return "Needs approval";
    case "completed":
      return "Completed";
    case "failed":
      return run.steps > 0 ? `Failed · step ${run.steps}` : "Failed";
    case "cancelled":
      return "Cancelled";
  }
}

function RunRow({ run, now, onOpen, onReview }: { run: RunView; now: number; onOpen: () => void; onReview: () => void }) {
  const waiting = run.status === "waiting_approval";
  return (
    <div className="osmo-list-row" data-status={run.status}>
      <StatusGlyph run={run} />
      <span className="osmo-list-title">Symphony · {run.task}</span>
      <span className={waiting ? "osmo-warn" : "osmo-muted"}>{statusText(run)}</span>
      <span className="osmo-mono osmo-tertiary">
        {run.status === "completed" && run.endedAt !== undefined ? `${Math.max(1, Math.round((now - run.endedAt) / 60_000))}m ago` : elapsed(run.startedAt, run.endedAt ?? now)}
      </span>
      {waiting ? (
        <button type="button" className="osmo-btn osmo-btn-primary osmo-btn-sm" onClick={onReview}>
          Review
        </button>
      ) : (
        <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={onOpen}>
          {run.status === "failed" ? "Open log" : "Open"}
        </button>
      )}
    </div>
  );
}

function StatusGlyph({ run }: { run: RunView }) {
  switch (run.status) {
    case "running":
      return (
        <span className="osmo-spin osmo-muted" aria-label="Running">
          ◌
        </span>
      );
    case "waiting_approval":
      return <span className="osmo-dot osmo-dot-warn" aria-label="Needs approval" />;
    case "completed":
      return (
        <span className="osmo-ok" aria-label="Completed">
          ✓
        </span>
      );
    default:
      return (
        <span className="osmo-err" aria-label="Failed">
          ✕
        </span>
      );
  }
}
