import { describe, expect, it } from "vitest";
import { notePath, snippetRuns, vaultApi } from "../src/lib/vault";

describe("note paths typed in the editor", () => {
  it("stay inside the vault and end in .md", () => {
    expect(notePath("Ideas/walking desk")).toBe("Ideas/walking desk.md");
    expect(notePath("  /Journal/2026-11-21.md ")).toBe("Journal/2026-11-21.md");
    expect(notePath("../../etc/passwd")).toBe("etc/passwd.md");
    expect(notePath("a\\..\\b")).toBe("a/b.md");
    expect(notePath(".obsidian/app")).toBeUndefined();
    expect(notePath("  ")).toBeUndefined();
  });
});

describe("search snippets", () => {
  it("split the index's [match] marks into runs", () => {
    expect(snippetRuns("Ship the [vault] [editor]. See")).toEqual([
      { text: "Ship the ", hit: false },
      { text: "vault", hit: true },
      { text: " ", hit: false },
      { text: "editor", hit: true },
      { text: ". See", hit: false },
    ]);
  });
});

describe("the vault API", () => {
  it("saves with PUT and surfaces the server's refusal", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const api = vaultApi(async (input, init) => {
      calls.push([input, init]);
      if (input.includes("escape")) return new Response(JSON.stringify({ detail: "'../escape.md' leaves the vault" }), { status: 400 });
      return new Response(JSON.stringify({ vault: "notes", path: "a.md", title: "A", text: "x", modified: 1, backlinks: [] }));
    });
    const saved = await api.save("notes", "a.md", "x", "replace");
    expect(saved.title).toBe("A");
    expect(calls[0]?.[1]?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ vault: "notes", path: "a.md", text: "x", mode: "replace" });
    await expect(api.note("notes", "../escape.md")).rejects.toThrow("leaves the vault");
  });
});
