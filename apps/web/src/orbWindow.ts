import { type ServerEvent, voiceSelectionSchema } from "@osmo/osmo-composer";
import type { StartOptions } from "./desktopBridge";
import { type Feed, emptyFeed, reduceFeed } from "./feed";
import type { ApprovalRequest } from "./lib/runs";

/** The orb window's approvals: what the server lists as waiting (authoritative, as of its last poll), and live events since. */
export type OrbFeedAction = { kind: "event"; event: ServerEvent } | { kind: "listed"; approvals: ApprovalRequest[] };

export function reduceOrbFeed(feed: Feed, action: OrbFeedAction): Feed {
  return action.kind === "event"
    ? reduceFeed(feed, action.event)
    : action.approvals.reduce<Feed>((next, approval) => reduceFeed(next, approval), emptyFeed);
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
