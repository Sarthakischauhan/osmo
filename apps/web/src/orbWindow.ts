import { type ServerEvent, voiceSelectionSchema } from "@osmo/osmo-composer";
import type { StartOptions } from "./desktopBridge";
import { type Feed, reduceFeed } from "./feed";
import type { ApprovalRequest } from "./lib/runs";

/**
 * The orb window's feed: the spoken lines and approvals the session's events carry, and the approvals the server lists
 * as waiting (authoritative, as of its last poll). A poll replaces the approvals and leaves the spoken lines alone;
 * `restart` is a different thread opening, whose lines are not the last thread's.
 */
export type OrbFeedAction = { kind: "event"; event: ServerEvent } | { kind: "listed"; approvals: ApprovalRequest[] } | { kind: "restart" };

const withoutSpoken = (feed: Feed): Feed => ({ ...feed, turns: feed.turns.filter((turn) => turn.role === "approval") });
const withoutApprovals = (feed: Feed): Feed => ({ ...feed, turns: feed.turns.filter((turn) => turn.role !== "approval") });

export function reduceOrbFeed(feed: Feed, action: OrbFeedAction): Feed {
  switch (action.kind) {
    case "event":
      return reduceFeed(feed, action.event);
    case "listed":
      return action.approvals.reduce<Feed>((next, approval) => reduceFeed(next, approval), withoutApprovals(feed));
    case "restart":
      return withoutSpoken(feed);
  }
}

/**
 * The voice, model and effort the workspace's picker last saved (`osmo.voice-pick.v2`), for a session the orb starts.
 * Not checked against the registry, which the orb does not have until a session is up; the server answers a bad pick.
 */
export function savedStart(saved: string | null): StartOptions {
  if (saved === null) return {};
  try {
    const parsed = voiceSelectionSchema.safeParse(JSON.parse(saved));
    return parsed.success ? parsed.data : {};
  } catch {
    return {};
  }
}
