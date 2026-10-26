import type { Thread } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { groupThreads, insertAfter, relativeTime, threadApi } from "../src/lib/threads";

const NOW = new Date(2026, 9, 26, 15, 0).getTime();
const at = (daysAgo: number, hour = 9) => new Date(2026, 9, 26 - daysAgo, hour, 12).getTime() / 1000;
const thread = (id: string, updatedAt: number): Thread => ({ id, title: id, createdAt: updatedAt, updatedAt });

describe("thread groups", () => {
  it("sorts threads newest first into Today, Yesterday, Previous 7 days and Older", () => {
    const groups = groupThreads([thread("old", at(30)), thread("today", at(0)), thread("week", at(3)), thread("yday", at(1))], NOW);
    expect(groups.map((g) => [g.group, g.threads.map((t) => t.id)])).toEqual([
      ["Today", ["today"]],
      ["Yesterday", ["yday"]],
      ["Previous 7 days", ["week"]],
      ["Older", ["old"]],
    ]);
  });

  it("leaves empty groups out", () => {
    expect(groupThreads([], NOW)).toEqual([]);
  });

  it("shows a time today, Yesterday, then the date", () => {
    expect(relativeTime(at(0), NOW)).toBe("9:12 AM");
    expect(relativeTime(at(1), NOW)).toBe("Yesterday");
    expect(relativeTime(at(3), NOW)).toBe("Oct 23");
  });

  it("places a fork directly below the thread it came from", () => {
    const list = [thread("a", 3), thread("b", 2), thread("c", 1)];
    expect(insertAfter(list, "b", thread("b2", 4)).map((t) => t.id)).toEqual(["a", "b", "b2", "c"]);
  });
});

describe("thread API", () => {
  it("lists, renames, forks and deletes through /api/threads", async () => {
    const calls: string[] = [];
    const api = threadApi(async (url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`.trim());
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      const body = url === "/api/threads" && !init?.method ? { threads: [thread("a", 1)] } : thread("a", 1);
      return Response.json(body);
    });
    expect((await api.list()).map((t) => t.id)).toEqual(["a"]);
    await api.rename("a", "Trip");
    await api.fork("a");
    await api.remove("a");
    expect(calls).toEqual(["GET /api/threads", 'PATCH /api/threads/a {"title":"Trip"}', "POST /api/threads/a/fork {}", "DELETE /api/threads/a"]);
  });

  it("fails on a server error", async () => {
    const api = threadApi(async () => new Response("nope", { status: 500 }));
    await expect(api.list()).rejects.toThrow("answered 500");
  });
});
