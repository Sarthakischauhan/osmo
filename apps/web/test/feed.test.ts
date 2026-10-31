import { BARGE_IN } from "@osmo/osmo-composer";
import { describe, expect, it } from "vitest";
import { conversationTitle, emptyFeed, pendingChoice, reduceFeed, feedFromMessages } from "../src/feed";

describe("conversationTitle", () => {
  it("uses the first thing said, and nothing while the conversation is empty", () => {
    expect(conversationTitle(emptyFeed.turns)).toBeUndefined();
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "", itemId: "u1" });
    expect(conversationTitle(feed.turns)).toBeUndefined();
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "Weather this weekend in Palo Alto?", itemId: "u1", final: true });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "assistant", text: "Sunny." });
    expect(conversationTitle(feed.turns)).toBe("Weather this weekend in Palo Alto");
  });

  it("stops on a word before the length cap", () => {
    const line = "Please look at the screen and tell me where the browser settings live exactly";
    const feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: line });
    const title = conversationTitle(feed.turns);
    expect(title).toBe("Please look at the screen and tell me where the browser");
    expect((title ?? "").length).toBeLessThanOrEqual(60);
  });
});

describe("reduceFeed", () => {
  it("ignores the barge-in audio flush signal, even while working", () => {
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "Plan Lisbon" });
    feed = reduceFeed(feed, { type: "state", state: "working", detail: "Planning" });
    const before = feed;
    feed = reduceFeed(feed, { type: "state", state: "working", detail: BARGE_IN });
    expect(feed).toBe(before);
  });

  it("keeps an utterance above its answer when the transcript lands mid-answer", () => {
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "", itemId: "u1" });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "assistant", text: "I just want to know, " });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "What do you mean?", itemId: "u1", final: true });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "assistant", text: "or just a chat." });
    expect(feed.turns.map((turn) => (turn.role === "approval" ? "" : `${turn.role}:${turn.text}`))).toEqual([
      "user:What do you mean?",
      "assistant:I just want to know, or just a chat.",
    ]);
    expect(feed.turns[0]).toMatchObject({ pending: false });
  });

  it("drops an utterance whose transcript comes back empty", () => {
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "", itemId: "u1" });
    expect(feed.turns[0]).toMatchObject({ role: "user", pending: true });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "", itemId: "u1", final: true });
    expect(feed.turns).toEqual([]);
  });

  it("groups assistant text, activity and widgets into one turn after the user turn", () => {
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "Weather in Palo Alto?" });
    feed = reduceFeed(feed, { type: "state", state: "working", detail: "Checking weather · Palo Alto" });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "PA", body: "72°" } } });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "assistant", text: "Sunny " });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "assistant", text: "Saturday." });
    expect(feed.turns).toHaveLength(2);
    const turn = feed.turns[1];
    expect(turn?.role).toBe("assistant");
    if (turn?.role !== "assistant") return;
    expect(turn.text).toBe("Sunny Saturday.");
    expect(turn.activities).toEqual([{ label: "Checking weather · Palo Alto", done: true }]);
    expect(turn.widgets.map((w) => w.id)).toEqual(["w1"]);
  });

  it("two widgets rendered in a row: an update for the second leaves the first untouched", () => {
    const weather = { id: "w-1111", type: "card", props: { title: "Palo Alto weather", body: "72°" } };
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "Weather and TSLA?" });
    feed = reduceFeed(feed, { type: "widget.render", widget: weather });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w-2222", type: "card", props: { title: "TSLA", body: "Loading" } } });
    const before = feed.turns[1];
    feed = reduceFeed(feed, { type: "transcript.delta", role: "assistant", text: "Pulling the price." });
    feed = reduceFeed(feed, { type: "widget.update", id: "w-2222", patch: { title: "TSLA $251" } });
    const turn = feed.turns[1];
    if (turn?.role !== "assistant" || before?.role !== "assistant") throw new Error("expected an assistant turn");
    expect(turn.widgets).toEqual([weather, { id: "w-2222", type: "card", props: { title: "TSLA $251", body: "Loading" } }]);
    expect(turn.widgets[0]).toBe(before.widgets[0]);
  });

  it("applies widget.update patches in place", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "a", body: "b" } } });
    feed = reduceFeed(feed, { type: "widget.update", id: "w1", patch: { title: "z" } });
    const turn = feed.turns[0];
    expect(turn?.role === "assistant" && turn.widgets[0]?.props).toEqual({ title: "z", body: "b" });
  });

  it("streams browser frames into the widget wherever the conversation has moved, then freezes it", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w-br", type: "browser", props: { status: "live" } } });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "How is it going?" });
    feed = reduceFeed(feed, { type: "widget.update", id: "w-br", patch: { frame: "data:image/jpeg;base64,AAAA" } });
    feed = reduceFeed(feed, { type: "widget.update", id: "w-br", patch: { frame: "data:image/jpeg;base64,BBBB" } });
    feed = reduceFeed(feed, { type: "widget.update", id: "w-br", patch: { status: "ended" } });
    const turn = feed.turns[0];
    expect(turn?.role === "assistant" && turn.widgets[0]?.props).toEqual({ status: "ended", frame: "data:image/jpeg;base64,BBBB" });
  });

  it("puts a browser widget back where it is when the server renders it again, and keeps other renders separate", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w-br", type: "browser", props: { status: "live", frame: "data:image/jpeg;base64,AAAA" } } });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "How is it going?" });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w-br", type: "browser", props: { status: "live", url: "http://a.test/", frame: "data:image/jpeg;base64,BBBB" } } });
    expect(feed.turns).toHaveLength(2);
    const turn = feed.turns[0];
    expect(turn?.role === "assistant" && turn.widgets).toEqual([
      { id: "w-br", type: "browser", props: { status: "live", url: "http://a.test/", frame: "data:image/jpeg;base64,BBBB" } },
    ]);
    // A browser the feed has not seen is added, as any widget.
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w-br2", type: "browser", props: { status: "live" } } });
    expect(feed.turns.flatMap((t) => (t.role === "assistant" ? t.widgets.map((w) => w.id) : []))).toEqual(["w-br", "w-br2"]);
  });

  it("an update with a reused id touches only the newest widget, never older ones above", () => {
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "first" });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "old", body: "b" } } });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "second" });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "new", body: "b" } } });
    const before = feed.turns[1];
    feed = reduceFeed(feed, { type: "widget.update", id: "w1", patch: { title: "patched" } });
    expect(feed.turns[1]).toBe(before);
    const titles = feed.turns.flatMap((t) => (t.role === "assistant" ? t.widgets.map((w) => w.props.title) : []));
    expect(titles).toEqual(["old", "patched"]);
  });

  it("an update targets the exact id among several widgets in the same turn", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w1-a", type: "card", props: { title: "a" } } });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w2-b", type: "card", props: { title: "b" } } });
    feed = reduceFeed(feed, { type: "widget.update", id: "w1-a", patch: { title: "a2" } });
    const turn = feed.turns[0];
    expect(turn?.role === "assistant" && turn.widgets.map((w) => w.props.title)).toEqual(["a2", "b"]);
  });

  it("a new render never mutates an older widget, and unknown ids are ignored", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w1-a", type: "card", props: { title: "a" } } });
    const first = feed.turns[0];
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "more" });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w2-b", type: "card", props: { title: "b" } } });
    expect(feed.turns[0]).toBe(first);
    const same = reduceFeed(feed, { type: "widget.update", id: "missing", patch: { title: "x" } });
    expect(same).toBe(feed);
  });
});

describe("pendingChoice", () => {
  const options = { prompt: "Pick", choices: [{ id: "a", label: "A" }, { id: "b", label: "B" }] };

  it("is the newest untapped options widget with no user turn after it", () => {
    let feed = reduceFeed(emptyFeed, { type: "transcript.delta", role: "user", text: "dinner?" });
    feed = reduceFeed(feed, { type: "widget.render", widget: { id: "w1-x", type: "options", props: options } });
    expect(pendingChoice(feed, new Set())).toBe("w1-x");
    expect(pendingChoice(feed, new Set(["w1-x"]))).toBeUndefined();
  });

  it("clears when the user answers by voice", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w1-x", type: "options", props: options } });
    feed = reduceFeed(feed, { type: "transcript.delta", role: "user", text: "the second one" });
    expect(pendingChoice(feed, new Set())).toBeUndefined();
  });

  it("counts cards only when they have buttons, and ignores plain widgets", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "c1", type: "card", props: { title: "t", body: "b" } } });
    expect(pendingChoice(feed, new Set())).toBeUndefined();
    feed = reduceFeed(feed, {
      type: "widget.render",
      widget: { id: "c2", type: "card", props: { title: "t", body: "b", actions: [{ id: "ok", label: "OK" }] } },
    });
    expect(pendingChoice(feed, new Set())).toBe("c2");
  });
});

describe("approval turns", () => {
  it("adds an approval to the feed and collapses it once answered", () => {
    const asked = reduceFeed(emptyFeed, {
      type: "approval.request",
      approvalId: "p1",
      runId: "r1",
      tool: "create_image",
      summary: "Drawing a picture",
      arguments: '{"prompt":"a cat"}',
    });
    expect(asked.turns).toMatchObject([{ role: "approval", approvalId: "p1", tool: "create_image" }]);
    const answered = reduceFeed(asked, { type: "approval.resolved", approvalId: "p1", runId: "r1", approved: true }, 42);
    expect(answered.turns[0]).toMatchObject({ resolved: { approved: true, at: 42 } });
  });

  it("rebuilds a stored thread's user and assistant turns", () => {
    const feed = feedFromMessages([
      { role: "user", text: "hi" },
      { role: "agent", text: "[agent r1] done" },
      { role: "assistant", text: "hello" },
    ]);
    expect(feed.turns.map((t) => t.role)).toEqual(["user", "assistant"]);
  });
});
