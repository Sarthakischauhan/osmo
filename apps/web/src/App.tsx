import { WidgetView } from "@osmo/chat-sdk-widgets";
import { OrbComposer, type ServerEvent, sameOriginSocketUrl, useOsmoSession } from "@osmo/osmo-composer";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type Feed, emptyFeed, reduceFeed } from "./feed";

export function App() {
  const [feed, dispatch] = useReducer((state: Feed, event: ServerEvent) => reduceFeed(state, event), emptyFeed);
  const onEvent = useCallback((event: ServerEvent) => dispatch(event), []);
  const session = useOsmoSession({ url: sameOriginSocketUrl(window.location), onEvent });
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [feed]);

  const connected = session.connection === "connected";
  const lastTurn = feed.turns[feed.turns.length - 1];

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

      <main className="canvas">
        <div className="feed">
          {feed.turns.length === 0 && (
            <div className="empty">
              <p>Ask about the weather, a stock, places nearby, or have it build something.</p>
            </div>
          )}
          {feed.turns.map((turn) =>
            turn.role === "user" ? (
              <div key={turn.key} className="turn user">
                <p className="bubble">{turn.text}</p>
                <span className="spoken">🎙 spoken</span>
              </div>
            ) : (
              <div key={turn.key} className="turn assistant">
                <span className="avatar" aria-hidden />
                <div className="assistant-body">
                  {turn.text && (
                    <p className="line">
                      {turn.text}
                      {turn === lastTurn && session.state === "speaking" && <span className="cursor" />}
                    </p>
                  )}
                  {turn.activities.map((activity, i) => (
                    <div key={i} className="activity">
                      <span className="activity-icon" aria-hidden>
                        ⌕
                      </span>
                      <span className="activity-label">{activity.label}</span>
                      {!activity.done && <span className="activity-status">Working…</span>}
                    </div>
                  ))}
                  {turn.widgets.map((widget) => (
                    <div key={widget.id} className="widget-slot">
                      <WidgetView
                        id={widget.id}
                        type={widget.type}
                        props={widget.props}
                        onAction={session.sendWidgetAction}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ),
          )}
          <div ref={bottom} />
        </div>
      </main>

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
