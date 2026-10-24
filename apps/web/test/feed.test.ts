import { describe, expect, it } from "vitest";
import { emptyFeed, reduceFeed } from "../src/feed";

describe("reduceFeed", () => {
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

  it("applies widget.update patches in place", () => {
    let feed = reduceFeed(emptyFeed, { type: "widget.render", widget: { id: "w1", type: "card", props: { title: "a", body: "b" } } });
    feed = reduceFeed(feed, { type: "widget.update", id: "w1", patch: { title: "z" } });
    const turn = feed.turns[0];
    expect(turn?.role === "assistant" && turn.widgets[0]?.props).toEqual({ title: "z", body: "b" });
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
