import { describe, expect, it } from "vitest";
import { noteReferences, vaultApi } from "../src/lib/vault";

describe("note references", () => {
  it("extracts typed wiki-links and ignores malformed or duplicate references", () => {
    expect(noteReferences(
      "Check [[chat:thread-1]] [[automation:daily_2]] [[event:meeting3]] " +
      "[[chat:thread-1]] [[chat:../../secret]] [[other:abc]]"
    )).toEqual([
      { kind: "chat", id: "thread-1" },
      { kind: "automation", id: "daily_2" },
      { kind: "event", id: "meeting3" },
    ]);
  });

  it("loads markdown issues with a status filter", async () => {
    const calls: string[] = [];
    const api = vaultApi(async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify({
        issues: [{ vault: "notes", path: "Ideas.md", title: "Ideas",
          task: "Fix microphone", status: "open", priority: "high", line: 3 }],
      }), { status: 200, headers: { "content-type": "application/json" } });
    });
    const issues = await api.issues("notes", "open");
    expect(issues[0]?.task).toBe("Fix microphone");
    expect(calls[0]).toContain("/api/vault/issues?");
    expect(calls[0]).toContain("status=open");
  });
});
