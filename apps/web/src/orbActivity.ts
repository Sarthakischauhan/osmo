import type { OsmoState } from "@osmo/osmo-composer";
import type { AgentActivity } from "./compact";
import type { Turn } from "./feed";

/** The live thought above the orb: the assistant turn a `thinking` state is still marking. */
export function liveThought(turns: readonly Turn[]): Extract<Turn, { role: "assistant" }> | undefined {
  const found = [...turns].reverse().find((turn) => turn.role === "assistant" && turn.thinking === true && Boolean(turn.reasoning?.trim()));
  return found?.role === "assistant" ? found : undefined;
}

/** Only show the cloud while real reasoning is streaming; otherwise leave the picker unobstructed. */
export function orbBubbleVisible(state: OsmoState, _activity: AgentActivity, thinking: boolean): boolean {
  return thinking && (state === "thinking" || state === "working");
}
