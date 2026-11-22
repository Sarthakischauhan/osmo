import type { ChangeEvent, ReactNode, RefObject } from "react";
import { BrowserControlsContext, type BrowserControls, osmoWidgets } from "@osmo/osmo-widgets";
import { WidgetProvider, type WidgetResponse } from "@sarchauhan/chat";
import { ScrollDirectionBar } from "@/components/ScrollDirectionBar";
import type { Theme } from "@/appearance";
import type { Runs } from "@/lib/runs";
import type { Feed } from "@/feed";
import { FeedTurn } from "@/FeedTurn";

type ChatFeedProps = {
  feed: Feed;
  runs: Runs;
  /** The other threads' runs, listed above the conversation. */
  runsList: ReactNode | undefined;
  theme: Theme;
  connected: boolean;
  browserControls: BrowserControls;
  respondToWidget: (response: WidgetResponse) => Promise<void>;
  onDecide: (approvalId: string, runId: string, approved: boolean) => Promise<void>;
  onChange: (event: ChangeEvent<HTMLElement>) => void;
  scrollRef: RefObject<HTMLElement>;
  bottomRef: RefObject<HTMLDivElement>;
};

/** The conversation: its turns and their widgets (answerable while a call is connected), with the scroll bar. */
export function ChatFeed({ feed, runs, runsList, theme, connected, browserControls, respondToWidget, onDecide, onChange, scrollRef, bottomRef }: ChatFeedProps) {
  return (
    <WidgetProvider widgets={osmoWidgets} respondToWidget={respondToWidget} disabled={!connected}>
      <BrowserControlsContext.Provider value={browserControls}>
        <div className="osmo-scroll-host">
          <main ref={scrollRef} className="canvas chat-root osmo-scroll" data-theme={theme} onChange={onChange}>
            {feed.turns.length === 0 ? (
              <div className="osmo-ascii-home chat-welcome"><h1 className="sr-only">New conversation</h1></div>
            ) : (
              <div className="feed">
                {runsList !== undefined && <div className="osmo-feed-runs">{runsList}</div>}
                {feed.turns.map((turn) => (
                  <FeedTurn key={turn.key} turn={turn} step={turn.role === "approval" ? runs[turn.runId]?.steps : undefined} onDecide={onDecide} />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </main>
          <ScrollDirectionBar target={scrollRef} label="Conversation scroll" />
        </div>
      </BrowserControlsContext.Provider>
    </WidgetProvider>
  );
}
