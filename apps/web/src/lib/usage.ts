import { type UsageRecord, usageRecordSchema } from "@osmo/osmo-composer";
import { z } from "zod";

/** `GET /api/usage`: what voice responses and agent turns used, newest first. */
export const usageListSchema = z.object({ since: z.number(), usage: z.array(usageRecordSchema) });

export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export async function fetchUsage(days: number, thread: string | undefined, fetcher: Fetcher = (input, init) => fetch(input, init)): Promise<UsageRecord[]> {
  const query = new URLSearchParams({ days: String(days) });
  if (thread !== undefined) query.set("thread", thread);
  const response = await fetcher(`/api/usage?${query}`);
  if (!response.ok) throw new Error(`Could not load usage (HTTP ${response.status})`);
  return usageListSchema.parse(await response.json()).usage;
}

export interface ModelUsage {
  model: string;
  responses: number;
  inputTokens: number;
  outputTokens: number;
  /** USD over the priced responses; `unpriced` counts the rest. */
  costUsd: number;
  unpriced: number;
}

export interface UsageSummary {
  responses: number;
  inputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  costUsd: number;
  /** Responses with no catalog price (voice today): their tokens count, their cost is n/a. */
  unpriced: number;
  /** Responses whose tokens the harness estimated because the provider reported none. */
  estimated: number;
  /** Cached share of input over the responses that reported cache use; null when none did. */
  cachedShare: number | null;
  byModel: ModelUsage[];
}

export function summarize(rows: readonly UsageRecord[]): UsageSummary {
  const models = new Map<string, ModelUsage>();
  let cachedInput = 0;
  let cachedOf = 0;
  let reported = false;
  const summary: UsageSummary = { responses: rows.length, inputTokens: 0, outputTokens: 0, reasoningTokens: 0, costUsd: 0, unpriced: 0, estimated: 0, cachedShare: null, byModel: [] };
  for (const row of rows) {
    summary.inputTokens += row.inputTokens;
    summary.outputTokens += row.outputTokens;
    summary.reasoningTokens += row.reasoningTokens;
    if (row.estimated) summary.estimated += 1;
    const model = models.get(row.model) ?? { model: row.model, responses: 0, inputTokens: 0, outputTokens: 0, costUsd: 0, unpriced: 0 };
    model.responses += 1;
    model.inputTokens += row.inputTokens;
    model.outputTokens += row.outputTokens;
    if (row.costUsd == null) {
      summary.unpriced += 1;
      model.unpriced += 1;
    } else {
      summary.costUsd += row.costUsd;
      model.costUsd += row.costUsd;
    }
    if (row.cachedTokens != null) {
      reported = true;
      cachedInput += row.cachedTokens;
      cachedOf += row.inputTokens;
    }
    models.set(row.model, model);
  }
  summary.cachedShare = reported && cachedOf > 0 ? cachedInput / cachedOf : null;
  summary.byModel = [...models.values()].sort((a, b) => b.costUsd - a.costUsd || b.responses - a.responses);
  return summary;
}

/** USD per local calendar day for the last `days` days, oldest first (days with nothing are 0). */
export function costByDay(rows: readonly UsageRecord[], days: number, now: Date = new Date()): { day: string; costUsd: number }[] {
  const key = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const totals = new Map<string, number>();
  for (let back = days - 1; back >= 0; back--) {
    totals.set(key(new Date(now.getFullYear(), now.getMonth(), now.getDate() - back)), 0);
  }
  for (const row of rows) {
    const day = key(new Date(row.createdAt * 1000));
    const total = totals.get(day);
    if (total !== undefined && row.costUsd != null) totals.set(day, total + row.costUsd);
  }
  return [...totals].map(([day, costUsd]) => ({ day, costUsd }));
}

/** `$0.04` above a cent, `$0.0042` below, `$0.00` at zero (core_ai `format_cost`). */
export function formatCost(amount: number): string {
  if (amount <= 0) return "$0.00";
  if (amount >= 0.01) return `$${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${amount.toFixed(4)}`;
}

export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}

/** A row's time: the clock time today, with the date on any other day. */
export function formatWhen(createdAt: number, now: Date = new Date()): string {
  const at = new Date(createdAt * 1000);
  const today = at.toDateString() === now.toDateString();
  return at.toLocaleString([], today ? { hour: "numeric", minute: "2-digit" } : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
