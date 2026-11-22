import type { UsageRecord } from "@osmo/osmo-composer";
import { Gauge } from "lucide-react";
import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { costByDay, fetchUsage, formatCost, formatTokens, formatWhen, summarize } from "@/lib/usage";

export const USAGE_RANGES = [7, 30] as const;
export type UsageRange = (typeof USAGE_RANGES)[number];
type Scope = "thread" | "all";
const RECENT_ROWS = 20;

export interface UsageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The open thread; "This thread" needs one. */
  threadId: string | undefined;
  /** The newest `usage` event from the session, added while the dialog is open. */
  live: UsageRecord | undefined;
}

/** Cost and usage: per-response rows, totals, cached share, a per-model split and cost per day. */
export function UsageDialog({ open, onOpenChange, threadId, live }: UsageDialogProps) {
  const [chosenScope, setScope] = useState<Scope>("thread");
  const scope: Scope = threadId === undefined ? "all" : chosenScope;
  const [range, setRange] = useState<UsageRange>(7);
  const [rows, setRows] = useState<UsageRecord[]>();
  const [error, setError] = useState<string>();
  const thread = scope === "thread" ? threadId : undefined;

  useEffect(() => {
    if (!open) return;
    let current = true;
    setRows(undefined);
    fetchUsage(range, thread)
      .then((loaded) => current && (setRows(loaded), setError(undefined)))
      .catch((failure: unknown) => current && setError(failure instanceof Error ? failure.message : "Could not load usage"));
    return () => {
      current = false;
    };
  }, [open, range, thread]);

  useEffect(() => {
    if (live !== undefined && (thread === undefined || live.threadId === thread)) setRows((previous) => previous && [live, ...previous]);
  }, [live, thread]);

  const summary = rows && summarize(rows);
  const days = rows && costByDay(rows, range);
  const peak = days ? Math.max(...days.map((day) => day.costUsd)) : 0;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="osmo-usage osmo-scroll max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader className="osmo-agent-settings-header">
          <div className="osmo-agent-settings-icon"><Gauge size={17} aria-hidden /></div>
          <DialogTitle>Usage</DialogTitle>
          <DialogDescription>What voice and the background agent used, priced from the model catalog.</DialogDescription>
        </DialogHeader>
        <div className="osmo-usage-controls">
          <div className="osmo-segmented" role="group" aria-label="Scope">
            <button type="button" aria-pressed={scope === "thread"} disabled={threadId === undefined} onClick={() => setScope("thread")}>This thread</button>
            <button type="button" aria-pressed={scope === "all"} onClick={() => setScope("all")}>All threads</button>
          </div>
          <div className="osmo-segmented" role="group" aria-label="Range">
            {USAGE_RANGES.map((days) => (
              <button key={days} type="button" aria-pressed={range === days} onClick={() => setRange(days)}>{days} days</button>
            ))}
          </div>
        </div>
        {error && <p className="osmo-agent-error" role="alert">{error}</p>}
        {summary && days && rows && (
          <>
            <section className="osmo-usage-stats" aria-label="Totals">
              <div><span>Cost</span><strong>{formatCost(summary.costUsd)}</strong><small>{summary.unpriced ? `${summary.unpriced} unpriced` : "all priced"}</small></div>
              <div><span>Tokens</span><strong>{formatTokens(summary.inputTokens + summary.outputTokens)}</strong><small>{formatTokens(summary.inputTokens)} in · {formatTokens(summary.outputTokens)} out</small></div>
              <div><span>Cached input</span><strong>{summary.cachedShare === null ? "n/a" : `${Math.round(summary.cachedShare * 100)}%`}</strong><small>{summary.cachedShare === null ? "not reported" : "of reported input"}</small></div>
              <div><span>Responses</span><strong>{summary.responses}</strong><small>{summary.estimated ? `${summary.estimated} estimated` : "as reported"}</small></div>
            </section>
            <section className="osmo-usage-section" aria-label="Cost per day">
              <h3>Cost per day</h3>
              <div className="osmo-usage-bars">
                {days.map((day) => (
                  <span key={day.day} title={`${day.day}: ${formatCost(day.costUsd)}`} style={{ height: `${peak > 0 ? Math.max(2, (day.costUsd / peak) * 100) : 2}%` }} />
                ))}
              </div>
            </section>
            <section className="osmo-usage-section" aria-label="By model">
              <h3>By model</h3>
              {summary.byModel.length === 0 ? (
                <p className="osmo-usage-empty">Nothing used in this range.</p>
              ) : (
                <table className="osmo-usage-table">
                  <thead><tr><th className="osmo-usage-left">Model</th><th>Responses</th><th>In</th><th>Out</th><th>Cost</th></tr></thead>
                  <tbody>
                    {summary.byModel.map((model) => (
                      <tr key={model.model}>
                        <td className="osmo-usage-left osmo-usage-model">{model.model}</td>
                        <td>{model.responses}</td>
                        <td>{formatTokens(model.inputTokens)}</td>
                        <td>{formatTokens(model.outputTokens)}</td>
                        <td>{model.unpriced === model.responses ? "n/a" : formatCost(model.costUsd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
            {rows.length > 0 && (
              <section className="osmo-usage-section" aria-label="Recent responses">
                <h3>Recent responses</h3>
                <table className="osmo-usage-table">
                  <thead><tr><th className="osmo-usage-left">Time</th><th className="osmo-usage-left">Source</th><th className="osmo-usage-left">Model</th><th>In</th><th>Cached</th><th>Out</th><th>Cost</th></tr></thead>
                  <tbody>
                    {rows.slice(0, RECENT_ROWS).map((row, index) => (
                      <tr key={`${row.createdAt}-${index}`}>
                        <td className="osmo-usage-left">{formatWhen(row.createdAt)}</td>
                        <td className="osmo-usage-left">{row.source === "voice" ? "Voice" : "Agent"}</td>
                        <td className="osmo-usage-left osmo-usage-model">{row.model}</td>
                        <td>{row.estimated ? "≈" : ""}{formatTokens(row.inputTokens)}</td>
                        <td>{row.cachedTokens == null ? "n/a" : formatTokens(row.cachedTokens)}</td>
                        <td>{formatTokens(row.outputTokens)}</td>
                        <td>{row.costUsd == null ? "n/a" : formatCost(row.costUsd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            <p className="osmo-usage-note">Voice responses are counted but not priced yet: realtime models bill audio and text separately and do not report the split. Agent cache reads are billed at the cache rate but their token count is not reported. ≈ marks tokens the agent estimated.</p>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
