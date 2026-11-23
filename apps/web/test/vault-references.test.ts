import { describe, expect, it } from "vitest";
import { frontmatterTags, withFrontmatterTags, noteReferences } from "../src/lib/vault";

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


  it("parses inline and list YAML frontmatter tags", () => {
    expect(frontmatterTags("---\\ntags: [osmo, agent, bug]\\nstatus: open\\n---\\n# Note")).toEqual(["osmo", "agent", "bug"]);
    expect(frontmatterTags("---\\nstatus: open\\ntags:\\n  - voice\\n  - latency\\n---\\n# Note")).toEqual(["voice", "latency"]);
    expect(frontmatterTags("# No frontmatter\\ntags: ignored")).toEqual([]);
  });

  it("updates tags without destroying other frontmatter fields or note content", () => {
    const original = "---\\nstatus: doing\\ntags:\\n  - old\\n  - beta\\npriority: high\\n---\\n# Work\\nText";
    const updated = withFrontmatterTags(original, ["agent", "voice"]);
    expect(frontmatterTags(updated)).toEqual(["agent", "voice"]);
    expect(updated).toContain("status: doing");
    expect(updated).toContain("priority: high");
    expect(updated).toContain("# Work\\nText");
    expect(updated).not.toContain("  - old");
    expect(withFrontmatterTags("# Plain note", ["work"])).toBe("---\\ntags: [work]\\n---\\n# Plain note");
    expect(withFrontmatterTags(updated, [])).not.toContain("tags:");
  });

  it("does not add frontmatter when clearing tags on a plain note", () => {
    expect(withFrontmatterTags("# Plain note", [])).toBe("# Plain note");
  });
});