import { agentRunStatusSchema, threadSchema, type Thread } from "@osmo/osmo-composer";
import { z } from "zod";

/** The server's stored threads (`/api/threads`, SQLite): the sidebar and the home screen's Recent. */

export const messageSchema = z.object({
  id: z.number(),
  threadId: z.string(),
  role: z.enum(["user", "assistant", "agent"]),
  text: z.string(),
  createdAt: z.number(),
});
export type ThreadMessage = z.infer<typeof messageSchema>;

export const agentRunSchema = z.object({
  runId: z.string(),
  threadId: z.string(),
  task: z.string(),
  status: agentRunStatusSchema,
  result: z.string().nullish(),
  createdAt: z.number(),
  updatedAt: z.number(),
});
export type AgentRun = z.infer<typeof agentRunSchema>;

export const threadDetailSchema = z.object({
  thread: threadSchema,
  messages: z.array(messageSchema),
  runs: z.array(agentRunSchema),
});
export type ThreadDetail = z.infer<typeof threadDetailSchema>;

const threadListSchema = z.object({ threads: z.array(threadSchema) });

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

async function call<T>(fetcher: Fetcher, url: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const response = await fetcher(url, init);
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${url} answered ${response.status}`);
  return schema.parse(await response.json());
}

function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function threadApi(fetcher: Fetcher = (url, init) => fetch(url, init)) {
  return {
    list: async () => (await call(fetcher, "/api/threads", threadListSchema)).threads,
    get: (id: string) => call(fetcher, `/api/threads/${encodeURIComponent(id)}`, threadDetailSchema),
    create: () => call(fetcher, "/api/threads", threadSchema, jsonBody("POST", {})),
    rename: (id: string, title: string) => call(fetcher, `/api/threads/${encodeURIComponent(id)}`, threadSchema, jsonBody("PATCH", { title })),
    fork: (id: string) => call(fetcher, `/api/threads/${encodeURIComponent(id)}/fork`, threadSchema, jsonBody("POST", {})),
    remove: async (id: string) => {
      const response = await fetcher(`/api/threads/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) throw new Error(`DELETE thread answered ${response.status}`);
    },
  };
}
export type ThreadApi = ReturnType<typeof threadApi>;

export type ThreadGroup = "Today" | "Yesterday" | "Previous 7 days" | "Older";
const GROUPS: ThreadGroup[] = ["Today", "Yesterday", "Previous 7 days", "Older"];
const DAY_MS = 86_400_000;

function startOfDay(ms: number): number {
  const day = new Date(ms);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
}

/** Which sidebar group a thread last updated at `updatedAt` (seconds) falls in. */
export function threadGroup(updatedAt: number, now: number): ThreadGroup {
  const today = startOfDay(now);
  const at = updatedAt * 1000;
  if (at >= today) return "Today";
  if (at >= today - DAY_MS) return "Yesterday";
  if (at >= today - 7 * DAY_MS) return "Previous 7 days";
  return "Older";
}

/** Threads newest first, in the sidebar's groups; empty groups are left out. */
export function groupThreads(threads: readonly Thread[], now: number): { group: ThreadGroup; threads: Thread[] }[] {
  const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt);
  return GROUPS.map((group) => ({ group, threads: sorted.filter((t) => threadGroup(t.updatedAt, now) === group) })).filter(
    (g) => g.threads.length > 0,
  );
}

/** A Recent row's time: "9:12 AM" today, "Yesterday", else "Oct 23". */
export function relativeTime(updatedAt: number, now: number): string {
  const at = new Date(updatedAt * 1000);
  const group = threadGroup(updatedAt, now);
  if (group === "Today") return at.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (group === "Yesterday") return "Yesterday";
  return at.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** The thread with `id` moved (or added) right after `after`, as fork places its copy directly below. */
export function insertAfter(threads: readonly Thread[], after: string, thread: Thread): Thread[] {
  const rest = threads.filter((t) => t.id !== thread.id);
  const at = rest.findIndex((t) => t.id === after);
  return at === -1 ? [thread, ...rest] : [...rest.slice(0, at + 1), thread, ...rest.slice(at + 1)];
}
