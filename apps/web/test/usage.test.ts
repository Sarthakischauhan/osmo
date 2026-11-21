import type { UsageRecord } from "@osmo/osmo-composer";
import { serverEventSchema } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { costByDay, fetchUsage, formatCost, formatTokens, formatWhen, summarize } from "../src/lib/usage";

const row = (over: Partial<UsageRecord>): UsageRecord => ({
  threadId: "t1",
  source: "agent",
  model: "grok:grok-4.7",
  inputTokens: 1000,
  outputTokens: 100,
  reasoningTokens: 0,
  estimated: false,
  createdAt: Date.UTC(2026, 10, 20, 12) / 1000,
  ...over,
});

describe("usage summary", () => {
  it("adds priced cost only, counts unpriced and estimated responses, and splits by model", () => {
    const summary = summarize([
      row({ costUsd: 0.002 }),
      row({ costUsd: 0.003, estimated: true }),
      row({ source: "voice", model: "xai:grok-voice-latest", inputTokens: 300, outputTokens: 50 }),
    ]);
    expect(summary.costUsd).toBeCloseTo(0.005);
    expect([summary.responses, summary.unpriced, summary.estimated, summary.inputTokens, summary.outputTokens]).toEqual([3, 1, 1, 2300, 250]);
    expect(summary.byModel.map((m) => [m.model, m.responses, m.unpriced])).toEqual([
      ["grok:grok-4.7", 2, 0],
      ["xai:grok-voice-latest", 1, 1],
    ]);
  });

  it("reports a cached share only over responses that reported cache use, else null", () => {
    expect(summarize([row({})]).cachedShare).toBeNull();
    expect(summarize([row({ cachedTokens: 600 }), row({})]).cachedShare).toBeCloseTo(0.6);
  });

  it("buckets priced cost by local day, oldest first, with empty days at 0", () => {
    const now = new Date(2026, 10, 21, 9);
    const today = new Date(2026, 10, 21, 8).getTime() / 1000;
    const twoDaysAgo = new Date(2026, 10, 19, 23).getTime() / 1000;
    const days = costByDay([row({ createdAt: today, costUsd: 0.01 }), row({ createdAt: today }), row({ createdAt: twoDaysAgo, costUsd: 0.02 })], 3, now);
    expect(days).toEqual([
      { day: "2026-11-19", costUsd: 0.02 },
      { day: "2026-11-20", costUsd: 0 },
      { day: "2026-11-21", costUsd: 0.01 },
    ]);
  });

  it("formats cost like core_ai and tokens compactly", () => {
    expect([formatCost(0), formatCost(0.0042), formatCost(0.04), formatCost(1234.5)]).toEqual(["$0.00", "$0.0042", "$0.04", "$1,234.50"]);
    expect([formatTokens(950), formatTokens(12_300), formatTokens(4_560_000)]).toEqual(["950", "12.3k", "4.6M"]);
    const now = new Date(2026, 10, 21, 15);
    expect(formatWhen(new Date(2026, 10, 21, 9, 5).getTime() / 1000, now)).not.toMatch(/Nov/);
    expect(formatWhen(new Date(2026, 10, 19, 9, 5).getTime() / 1000, now)).toMatch(/Nov/);
  });
});

describe("usage API and event", () => {
  it("asks for the range and thread and parses rows the server sent with nulls", async () => {
    let asked = "";
    const rows = await fetchUsage(7, "t1", async (input) => {
      asked = input;
      return new Response(JSON.stringify({ since: 0, usage: [{ ...row({}), runId: null, cachedTokens: null, costUsd: null }] }));
    });
    expect(asked).toBe("/api/usage?days=7&thread=t1");
    expect(rows[0]?.costUsd).toBeNull();
    await expect(fetchUsage(7, undefined, async () => new Response("", { status: 500 }))).rejects.toThrow("HTTP 500");
  });

  it("accepts the live usage event without the fields the server leaves out", () => {
    const parsed = serverEventSchema.parse({ type: "usage", usage: { threadId: "t1", source: "voice", model: "xai:grok-voice-latest", inputTokens: 3, outputTokens: 1, createdAt: 1 } });
    expect(parsed.type === "usage" && [parsed.usage.costUsd, parsed.usage.reasoningTokens, parsed.usage.estimated]).toEqual([undefined, 0, false]);
  });
});
