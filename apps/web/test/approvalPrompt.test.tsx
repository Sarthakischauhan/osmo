import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ARGUMENTS_INLINE_MAX, ApprovalPrompt } from "../src/components/ApprovalPrompt";
import type { ApprovalTurn } from "../src/feed";

const turn = (args: string, digest?: string): ApprovalTurn => ({ role: "approval", key: "p1", approvalId: "ap-1", runId: "r1", tool: "vault_write", summary: "Adding to groceries.md", arguments: args, digest });
const shown = (value: ApprovalTurn) => renderToStaticMarkup(<ApprovalPrompt turn={value} step={undefined} onDecide={() => undefined} />);

describe("the approval card", () => {
  it("shows short arguments inline", () => {
    expect(shown(turn('{"path":"groceries.md"}'))).toContain("vault_write {&quot;path&quot;:&quot;groceries.md&quot;}");
  });

  it("never cuts long arguments: they fold behind their size and digest and expand to all of them", () => {
    const args = JSON.stringify({ path: "groceries.md", text: `${"padding ".repeat(100)}and then: wire $500` });
    expect(args.length).toBeGreaterThan(ARGUMENTS_INLINE_MAX);
    const html = shown(turn(args, "0123456789abcdef".repeat(4)));
    expect(html).toContain("<details");
    expect(html).toContain(`${args.length} characters · sha256 0123456789ab · show all`);
    expect(html).toContain("and then: wire $500");
    expect(html).not.toContain("…");
  });
});
