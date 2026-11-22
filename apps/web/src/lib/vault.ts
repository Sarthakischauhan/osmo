import { z } from "zod";
import type { Fetcher } from "./usage";

/** The editor's side of `/api/vault` (symphony_server/vault_api.py): the user's own reads and saves. */
export const vaultListSchema = z.object({ vaults: z.array(z.object({ name: z.string(), root: z.string() })) });
export const noteEntrySchema = z.object({ vault: z.string(), path: z.string(), title: z.string(), modified: z.number(), size: z.number() });
export const noteHitSchema = z.object({ vault: z.string(), path: z.string(), title: z.string(), snippet: z.string() });
export const noteSchema = z.object({
  vault: z.string(),
  path: z.string(),
  title: z.string(),
  text: z.string(),
  modified: z.number(),
  backlinks: z.array(z.string()),
});

export type VaultInfo = z.infer<typeof vaultListSchema>["vaults"][number];
export type NoteEntry = z.infer<typeof noteEntrySchema>;
export type NoteHit = z.infer<typeof noteHitSchema>;
export type Note = z.infer<typeof noteSchema>;

const browserFetch: Fetcher = (input, init) => fetch(input, init);

async function read<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined);
    const detail = z.object({ detail: z.string() }).safeParse(body);
    throw new Error(detail.success ? detail.data.detail : `HTTP ${response.status}`);
  }
  return schema.parse(await response.json());
}

export function vaultApi(fetcher: Fetcher = browserFetch) {
  return {
    async vaults(): Promise<VaultInfo[]> {
      return (await read(await fetcher("/api/vault"), vaultListSchema)).vaults;
    },
    async notes(vault: string): Promise<NoteEntry[]> {
      const query = new URLSearchParams({ vault });
      return (await read(await fetcher(`/api/vault/notes?${query}`), z.object({ notes: z.array(noteEntrySchema) }))).notes;
    },
    async search(vault: string, q: string): Promise<NoteHit[]> {
      const query = new URLSearchParams({ vault, q });
      return (await read(await fetcher(`/api/vault/search?${query}`), z.object({ results: z.array(noteHitSchema) }))).results;
    },
    async note(vault: string, path: string): Promise<Note> {
      const query = new URLSearchParams({ vault, path });
      return read(await fetcher(`/api/vault/note?${query}`), noteSchema);
    },
    async save(vault: string, path: string, text: string, mode: "create" | "replace"): Promise<Note> {
      const init = { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ vault, path, text, mode }) };
      return read(await fetcher("/api/vault/note", init), noteSchema);
    },
  };
}

export type VaultApi = ReturnType<typeof vaultApi>;

/** A note path for a title the user typed: kept inside the vault (no leading slash, no `..`) and ending in .md. */
export function notePath(input: string): string | undefined {
  const parts = input
    .trim()
    .replace(/\\/g, "/")
    .split("/")
    .map((part) => part.trim())
    .filter((part) => part !== "" && part !== "." && part !== "..");
  const last = parts.pop();
  if (last === undefined || [...parts, last].some((part) => part.startsWith("."))) return undefined;
  return [...parts, last.toLowerCase().endsWith(".md") ? last : `${last}.md`].join("/");
}

/** A snippet's `[match]` marks as text runs, so the list can bold them without HTML. */
export function snippetRuns(snippet: string): { text: string; hit: boolean }[] {
  return snippet
    .split(/(\[[^\]]*\])/)
    .filter((part) => part !== "")
    .map((part) => (part.startsWith("[") && part.endsWith("]") ? { text: part.slice(1, -1), hit: true } : { text: part, hit: false }));
}
