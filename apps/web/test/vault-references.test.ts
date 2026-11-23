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


});
