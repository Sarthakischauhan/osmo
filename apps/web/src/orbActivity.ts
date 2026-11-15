import type { OsmoState } from "@osmo/osmo-composer";
import type { AgentActivity } from "./compact";
import type { Turn } from "./feed";

/** The live thought above the orb: the assistant turn a `thinking` state is still marking. */
export function liveThought(turns: readonly Turn[]): Extract<Turn, { role: "assistant" }> | undefined {
  const found = [...turns].reverse().find((turn) => turn.role === "assistant" && turn.thinking === true);
  return found?.role === "assistant" ? found : undefined;
}

/**
 * Whether the bubble above the orb should exist. Same for every provider: only while the
 * agent is working (thinking, a tool, or a run still going). Hover, idle, listening and
 * speaking do not open it.
 */
export function orbBubbleVisible(state: OsmoState, activity: AgentActivity, thinking: boolean): boolean {
  const working = state === "thinking" || state === "working" || activity.running > 0 || activity.needingApproval > 0 || activity.elsewhere > 0;
  return working && (thinking || activity.cards.length > 0 || activity.elsewhere > 0);
}
