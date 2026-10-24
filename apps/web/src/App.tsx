import { osmoWidgets, toWidgetPart } from "@osmo/osmo-widgets";
import { MessageContent, WidgetProvider, type WidgetResponse } from "@sarchauhan/chat";
import "@sarchauhan/chat/styles.css";
import type { AgentPart } from "@sarchauhan/protocol";
import { OrbComposer, type ServerEvent, sameOriginSocketUrl, useOsmoSession } from "@osmo/osmo-composer";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type Feed, type Turn, emptyFeed, reduceFeed } from "./feed";

export function App() {
  const [feed, dispatch] = useReducer((state: Feed, event: ServerEvent) => reduceFeed(state, event), emptyFeed);
  const onEvent = useCallback((event: ServerEvent) => dispatch(event), []);
  const session = useOsmoSession({ url: sameOriginSocketUrl(window.location), onEvent });
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [feed]);

  const connected = session.connection === "connected";
  const { sendWidgetAction } = session;
  const respondToWidget = useCallback(
    async (response: WidgetResponse) => {
      if (response.widgetId === undefined || response.actionId === undefined || typeof response.value !== "string") return;
      sendWidgetAction(response.widgetId, response.actionId, response.value);
    },
    [sendWidgetAction],
  );

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
        <main className="canvas chat-root" data-theme="dark">
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
        <OrbComposer session={session} />
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
