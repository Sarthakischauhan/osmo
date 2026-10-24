import { osmoWidgets, toWidgetPart } from "@osmo/osmo-widgets";
import { MessageContent, WidgetProvider, type WidgetResponse } from "@sarchauhan/chat";
import "@sarchauhan/chat/styles.css";
import type { AgentPart } from "@sarchauhan/protocol";
import { OrbComposer, type ServerEvent, orbMode, sameOriginSocketUrl, useOsmoSession } from "@osmo/osmo-composer";
import { Cues } from "./cues";
import { type ChangeEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type Feed, type Turn, emptyFeed, pendingChoice, reduceFeed } from "./feed";

export function App() {
  const [feed, dispatch] = useReducer((state: Feed, event: ServerEvent) => reduceFeed(state, event), emptyFeed);
  const speaking = useRef(false);
  const cues = useRef<Cues | null>(null);
  cues.current ??= new Cues(() => speaking.current);
  const onEvent = useCallback((event: ServerEvent) => {
    dispatch(event);
    cues.current?.onServerEvent(event);
  }, []);
  const session = useOsmoSession({ url: sameOriginSocketUrl(window.location), onEvent });
  speaking.current = session.state === "speaking";
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [feed]);

  const connected = session.connection === "connected";
  const { sendWidgetAction } = session;
  const [answered, setAnswered] = useState<ReadonlySet<string>>(() => new Set());
  const respondToWidget = useCallback(
    async (response: WidgetResponse) => {
      if (response.widgetId === undefined || response.actionId === undefined || typeof response.value !== "string") return;
      const id = response.widgetId;
      setAnswered((previous) => new Set(previous).add(id));
      cues.current?.onConfirm();
      sendWidgetAction(id, response.actionId, response.value);
    },
    [sendWidgetAction],
  );

  // Autoplay rules: cues stay silent until the first real gesture unlocks audio.
  useEffect(() => {
    const unlock = () => void cues.current?.gesture();
    document.addEventListener("pointerdown", unlock, true);
    document.addEventListener("keydown", unlock, true);
    return () => {
      document.removeEventListener("pointerdown", unlock, true);
      document.removeEventListener("keydown", unlock, true);
    };
  }, []);
  useEffect(() => cues.current?.onConnection(session.connection), [session.connection]);
  const pending = connected ? pendingChoice(feed, answered) : undefined;
  const amber = orbMode(session.connection, session.state, pending !== undefined) === "awaiting";
  useEffect(() => cues.current?.onAmber(amber ? pending : undefined), [amber, pending]);
  const onFeedChange = useCallback((event: ChangeEvent<HTMLElement>) => {
    const target = event.target;
    if (target instanceof HTMLInputElement && target.type === "radio" && target.closest(".chat-question")) {
      cues.current?.onOptionPick();
    }
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>Osmo</strong>
        </div>
        <div className="topbar-right">
          {session.startedAt !== undefined && <Clock since={session.startedAt} />}
          {connected && (
            <button type="button" className="end" onClick={session.end}>
              <span aria-hidden>✕</span> End
            </button>
          )}
        </div>
      </header>

      <WidgetProvider widgets={osmoWidgets} respondToWidget={respondToWidget} disabled={!connected}>
        <main className="canvas chat-root" data-theme="dark" onChange={onFeedChange}>
          <div className="feed">
            {feed.turns.length === 0 && (
              <div className="empty">
                <p>Ask about the weather, a stock, places nearby, or have it build something.</p>
              </div>
            )}
            {feed.turns.map((turn) =>
              turn.role === "user" ? (
                <div key={turn.key} className="turn user">
                  <div className="bubble">
                    <MessageContent parts={[{ type: "text", text: turn.text }]} isUser />
                  </div>
                  <span className="spoken">🎙 spoken</span>
                </div>
              ) : (
                <div key={turn.key} className="turn assistant">
                  <span className="avatar" aria-hidden />
                  <div className="assistant-body">
                    <MessageContent parts={assistantParts(turn)} />
                  </div>
                </div>
              ),
            )}
            <div ref={bottom} />
          </div>
        </main>
      </WidgetProvider>

      <footer className="dock">
        <OrbComposer session={session} awaitingChoice={pending !== undefined} />
      </footer>
    </div>
  );
}

function Clock({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return <span className="clock">{`${mm}:${ss}`}</span>;
}

/** One assistant turn as chat-sdk message parts: the spoken line, tool activity rows, then widgets. */
function assistantParts(turn: Extract<Turn, { role: "assistant" }>): AgentPart[] {
  const parts: AgentPart[] = [];
  if (turn.text) parts.push({ type: "text", text: turn.text });
  turn.activities.forEach((activity, i) =>
    parts.push({
      type: "tool",
      toolName: "activity",
      toolCallId: `${turn.key}-tool-${i}`,
      title: activity.label,
      state: activity.done ? "output-available" : "input-available",
    }),
  );
  for (const widget of turn.widgets) {
    const part = toWidgetPart(widget.id, widget.type, widget.props);
    if (part) parts.push(part);
  }
  return parts;
}
