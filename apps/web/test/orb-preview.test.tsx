import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OrbPreview } from "../src/OrbPreview";
import { type Turn } from "../src/feed";
import { agentActivity } from "../src/compact";

const quiet = agentActivity({}, [], undefined, 0);
const user = (text: string, key: string): Turn => ({ role: "user", text, key });
const assistant = (text: string, key: string, reasoning?: string): Turn => ({
  role: "assistant", key, text, reasoning, thinking: !!reasoning, activities: [], runs: [], widgets: [],
});
function preview(turns: Turn[], voiceState: "idle" | "thinking" | "working" | "speaking" = "idle", detail?: string) {
  return renderToStaticMarkup(<OrbPreview turns={turns} runs={{}} activity={quiet}
    onDecide={async () => undefined} voiceState={voiceState} detail={detail} />);
}

describe("desktop orb transcript", () => {
  it("displays speaker-labelled recent turns as readable transcript lines", () => {
    const html = preview([user("Can you look this up?", "u1"), assistant("Yes, here are the details.", "a1")]);
    expect(html).toContain("Conversation");
    expect(html).toContain("You");
    expect(html).toContain("Osmo");
    expect(html).toContain("Can you look this up?");
    expect(html).toContain("Yes, here are the details.");
    expect(html).not.toContain("Live assistant activity");
  });

  it("shows streamed thinking text while the voice model is thinking", () => {
    const html = preview([user("Research this", "u1"), assistant("", "a1", "Checking first source")], "thinking");
    expect(html).toContain("Live assistant activity");
    expect(html).toContain("Checking first source");
    expect(html).toContain("Live");
    // Reasoning belongs to the live block, not to an empty chat bubble.
    expect(html.match(/Checking first source/g)).toHaveLength(1);
  });

  it("shows tool progress when working without streamed reasoning", () => {
    const html = preview([user("Find it", "u1")], "working", "Searching files");
    expect(html).toContain("Searching files");
    expect(html).toContain("Working on your request");
  });

  it("does not leak a finished thinking trace into ordinary speaking view", () => {
    const html = preview([assistant("Here is the result", "a1", "Private thinking tokens")], "speaking");
    expect(html).toContain("Here is the result");
    expect(html).not.toContain("Private thinking tokens");
  });

  it("caps transcript to eight recent turns", () => {
    const html = preview(Array.from({ length: 12 }, (_, n) => user("utterance-" + n, String(n))));
    expect(html).not.toContain("utterance-0");
    expect(html).toContain("utterance-11");
  });
});
