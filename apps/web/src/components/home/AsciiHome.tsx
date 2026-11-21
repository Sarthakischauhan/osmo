export type SessionConnection = "disconnected" | "connecting" | "connected";
export type SessionVoiceState = "idle" | "listening" | "thinking" | "speaking" | "working";

/** Time-of-day greeting for the empty-thread opening screen. */
export function greeting(now: number, name: string | undefined): string {
  const hour = new Date(now).getHours();
  const part = hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}` : part;
}

/**
 * Short cue under the greeting. Tracks live session connection and voice state so it
 * stays aligned with the dock orb — never a second control, only status copy.
 */
export function openingCue(connection: SessionConnection, state: SessionVoiceState = "idle"): string {
  if (connection === "connecting") return "Opening your session…";
  if (connection === "disconnected") return "Tap the orb or press Space to talk";
  switch (state) {
    case "listening":
      return "Listening · just talk";
    case "thinking":
      return "Thinking…";
    case "working":
      return "Working…";
    case "speaking":
      return "Speaking · tap the orb to interrupt";
    default:
      return "Ready · tap the orb or press Space";
  }
}

/**
 * Empty-thread opening: nothing but the orb, which the layout holds at the centre of the screen
 * over the voice field (and its picker) until the first turn docks it.
 */
export function AsciiHome() {
  return (
    <div className="osmo-ascii-home chat-welcome">
      <h1 className="sr-only">New conversation</h1>
    </div>
  );
}
