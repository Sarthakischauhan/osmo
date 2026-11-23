import { z } from "zod";
import type { Fetcher } from "./usage";

/** The editor's side of `/api/vault` (symphony_server/vault_api.py): the user's own reads and saves. */
export const vaultListSchema = z.object({ vaults: z.array(z.object({ name: z.string(), root: z.string() })) });
export const noteEntrySchema = z.object({ vault: z.string(), path: z.string(), title: z.string(), modified: z.number(), size: z.number(), tags: z.array(z.string()).default([]) });
export const noteHitSchema = z.object({ vault: z.string(), path: z.string(), title: z.string(), snippet: z.string(), tags: z.array(z.string()).default([]) });
export const noteSchema = z.object({
  vault: z.string(),
  path: z.string(),
  title: z.string(),
  text: z.string(),
  modified: z.number(),
  backlinks: z.array(z.string()),
  tags: z.array(z.string()).default([]),
});

export type VaultInfo = z.infer<typeof vaultListSchema>["vaults"][number];
export type NoteEntry = z.infer<typeof noteEntrySchema>;
export type NoteHit = z.infer<typeof noteHitSchema>;
export type Note = z.infer<typeof noteSchema>;

/** Parse just frontmatter tags; supports YAML inline and dash-list forms. */
export function frontmatterTags(markdown: string): string[] {
  const match = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(markdown);
  if (!match) return [];
  const lines = match[1]!.split(/\r?\n/);
  const index = lines.findIndex((line) => /^tags:[ \t]*/.test(line));
  if (index < 0) return [];
  const raw = lines[index]!.slice(5).trim();
  const values = raw.startsWith("[") && raw.endsWith("]")
    ? raw.slice(1, -1).split(",")
    : raw ? [raw] : [];
  // Consume dash-list items until the first non-list line.
  const items = raw === "" ? [] as string[] : values;
  if (raw === "") for (const line of lines.slice(index + 1)) {
    const item = /^[ \t]*-[ \t]+(.+?)[ \t]*$/.exec(line);
    if (!item) break;
    items.push(item[1]!);
  }
  return [...new Set(items.map((tag) => tag.trim().replace(/^['"]|['"]$/g, "").replace(/^#/, "").trim())
    .filter((tag) => tag.length > 0 && tag.length <= 64))].slice(0, 32);
}

/** Update only the tags frontmatter field, preserving all other metadata and body. */
export function withFrontmatterTags(markdown: string, tags: string[]): string {
  const clean = [...new Set(tags.map((tag) => tag.trim().replace(/^#/, ""))
    .filter((tag) => /^[\p{L}\p{N}_/-]{1,64}$/u.test(tag)))].slice(0, 32);
  const match = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(\r?\n|$)/.exec(markdown);
  const declaration = "tags: [" + clean.join(", ") + "]";
  if (!match) return clean.length ? "---\n" + declaration + "\n---\n" + markdown : markdown;
  const lines = match[1]!.split(/\r?\n/);
  const index = lines.findIndex((line) => /^tags:[ \t]*/.test(line));
  if (index >= 0) {
    let end = index + 1;
    if (/^tags:[ \t]*$/.test(lines[index]!)) {
      while (end < lines.length && /^[ \t]*-[ \t]+/.test(lines[end]!)) end++;
    }
    lines.splice(index, end - index, ...(clean.length ? [declaration] : []));
  } else if (clean.length) {
    lines.push(declaration);
  }
  return "---\n" + lines.join("\n") + "\n---" + (match[2] || "\n") + markdown.slice(match[0].length);
}

export type NoteReference = { kind: "chat" | "automation" | "event"; id: string };
/** Stable IDs written as wiki-links in note text, e.g. [[chat:abc123]]. */
export function noteReferences(text: string): NoteReference[] {
  const matches = text.matchAll(/\[\[(chat|automation|event):([a-zA-Z0-9_-]{1,128})\]\]/g);
  const seen = new Set<string>();
  const refs: NoteReference[] = [];
  for (const match of matches) {
    const kind = match[1] as NoteReference["kind"];
    const id = match[2]!;
    if (seen.has(kind + ":" + id)) continue;
    seen.add(kind + ":" + id);
    refs.push({ kind, id });
  }
  return refs;
}

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
