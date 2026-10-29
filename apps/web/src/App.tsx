import { DesktopSupport, useDesktopSupport } from "./desktopSupport";
import { useAppearance, type Theme } from "./appearance";
import { BrowserControlsContext, osmoWidgets, toWidgetPart } from "@osmo/osmo-widgets";
import { MessageContent, WidgetProvider, type WidgetResponse } from "@sarchauhan/chat";
import "@sarchauhan/chat/styles.css";
import type { AgentPart, AgentSubagent, AgentSubagentState } from "@sarchauhan/protocol";
import {
  OrbComposer,
  type ServerEvent,
  type Thread,
  type VoiceModel,
  type VoiceSelection,
  orbMode,
  sameOriginSocketUrl,
  usePersistedPick,
} from "@osmo/osmo-composer";
import "@osmo/osmo-composer/radial-picker.css";
import { Toaster } from "@/components/ui/sonner";
import { SidebarInset, SidebarProvider, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApprovalPrompt } from "@/components/ApprovalPrompt";
import { ThreadSidebar } from "@/components/ThreadSidebar";
import { RunsList } from "@/components/home/RunsList";
import { AsciiHome } from "@/components/home/AsciiHome";
import { type ApprovalRequest, COMPLETED_LINGER_MS, type RunView, type Runs, decideApproval, fetchRuns, mergeListed, reduceRuns, visibleRuns } from "@/lib/runs";
import { insertAfter, threadApi } from "@/lib/threads";
import { toast } from "sonner";
import { ConnectProviderDialog, type ProviderInfo, lockedProviderAction } from "./ConnectProvider";
import { useServerCatalog } from "./serverCatalog";
import { Cues } from "./cues";
import { type ChangeEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useSession } from "./session";
import { type Feed, type RunCard, type Turn, emptyFeed, feedFromMessages, pendingChoice, reduceFeed } from "./feed";

const NO_PROVIDERS: ProviderInfo[] = [];

const USER_NAME: string | undefined = import.meta.env.VITE_OSMO_USER_NAME || undefined;
const api = threadApi();

type FeedAction = { kind: "event"; event: ServerEvent } | { kind: "load"; feed: Feed };
function feedReducer(state: Feed, action: FeedAction): Feed {
  return action.kind === "load" ? action.feed : reduceFeed(state, action.event);
}

export function App() {
  const [theme, setTheme] = useAppearance();
  return (
    <TooltipProvider delayDuration={300}>
      <SidebarProvider>
        <Osmo theme={theme} setTheme={setTheme} />
      </SidebarProvider>
    </TooltipProvider>
  );
}

function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function Osmo({ theme, setTheme }: { theme: Theme; setTheme: (theme: Theme) => void }) {
  const desktop = useDesktopSupport();
  const [feed, dispatchFeed] = useReducer(feedReducer, emptyFeed);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadId, setThreadId] = useState<string>();
  const [runs, setRuns] = useState<Runs>({});
  const approvals = useRef<ApprovalRequest[]>([]);
  const now = useNow();
  const sidebar = useSidebar();
  const speaking = useRef(false);
  const cues = useRef<Cues | null>(null);
  cues.current ??= new Cues(() => speaking.current);
  const savePick = useRef<(next: VoiceSelection) => void>();
  const onEvent = useCallback((event: ServerEvent) => {
    dispatchFeed({ kind: "event", event });
    setRuns((previous) => reduceRuns(previous, event, Date.now()));
    if (event.type === "thread") {
      setThreadId(event.thread.id);
      setThreads((previous) => [event.thread, ...previous.filter((t) => t.id !== event.thread.id)]);
    }
    cues.current?.onServerEvent(event);
    // What the server applied (session start, picker, or `set_voice`), or on error what is still in effect.
    if (event.type === "voice.configured") savePick.current?.(event.selection);
  }, []);
  const [registry, setRegistry] = useState<VoiceModel[]>([]);
  const [pick, save, pickMemory] = usePersistedPick(registry);
  savePick.current = save;
  const session = useSession({
    url: sameOriginSocketUrl(window.location),
    model: pick?.model,
    voice: pick?.voice,
    effort: pick?.effort,
    threadId,
    onEvent,
  });
  const { end } = session;
  useEffect(() => {
    api.list().then(setThreads, () => toast.error("Could not load threads"));
  }, []);
  const openThread = useCallback(
    async (id: string | undefined) => {
      end();
      void window.osmoDesktop?.setEnabled(false);
      setThreadId(id);
      if (id === undefined) {
        dispatchFeed({ kind: "load", feed: emptyFeed });
        return;
      }
      try {
        const detail = await api.get(id);
        const ownRuns = new Set(detail.runs.map((run) => run.runId));
        // Approvals its runs still wait for go back into the stream.
        const waiting = approvals.current.filter((approval) => ownRuns.has(approval.runId));
        dispatchFeed({ kind: "load", feed: waiting.reduce((shown, approval) => reduceFeed(shown, approval), feedFromMessages(detail.messages, detail.runs)) });
      } catch {
        toast.error("Could not open that thread");
      }
    },
    [end],
  );
  const renameThread = useCallback(async (id: string, title: string) => {
    try {
      const renamed = await api.rename(id, title);
      setThreads((previous) => previous.map((t) => (t.id === id ? renamed : t)));
    } catch {
      toast.error("Could not rename the thread");
    }
  }, []);
  const forkThread = useCallback(
    async (id: string) => {
      try {
        const copy = await api.fork(id);
        setThreads((previous) => insertAfter(previous, id, copy));
        await openThread(copy.id);
        return copy;
      } catch {
        toast.error("Could not fork the thread");
        return undefined;
      }
    },
    [openThread],
  );
  const deleteThread = useCallback(
    async (id: string) => {
      try {
        await api.remove(id);
        setThreads((previous) => previous.filter((t) => t.id !== id));
        if (id === threadId) await openThread(undefined);
      } catch {
        toast.error("Could not delete the thread");
      }
    },
    [openThread, threadId],
  );
  const countMessages = useCallback(async (id: string) => (await api.get(id)).messages.length, []);
  // The socket only echoes approval.resolved while a session is live, so record a decision the server accepted here.
  const decide = useCallback(async (approvalId: string, runId: string, approved: boolean) => {
    if (await decideApproval(approvalId, approved)) dispatchFeed({ kind: "event", event: { type: "approval.resolved", approvalId, runId, approved } });
    else toast.error("That approval is no longer pending");
  }, []);
  // Every thread's runs (the socket only reports the open thread's): polled for the Running list.
  useEffect(() => {
    const poll = () =>
      fetchRuns(Date.now() - COMPLETED_LINGER_MS).then(
        (listed) => {
          approvals.current = listed.approvals;
          setRuns((previous) => mergeListed(previous, listed));
        },
        () => undefined,
      );
    void poll();
    const timer = window.setInterval(poll, 3000);
    return () => window.clearInterval(timer);
  }, []);
  const review = useCallback(
    async (run: RunView) => {
      if (run.threadId !== undefined && run.threadId !== threadId) await openThread(run.threadId);
      window.setTimeout(() => document.getElementById(`approval-${run.approvalId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
    },
    [openThread, threadId],
  );
  // ⌘K: a new thread (Home); Space: talk, or interrupt while Osmo speaks.
  const { start, interrupt, connection } = session;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        void openThread(undefined);
        return;
      }
      const target = event.target;
      const typing = target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "BUTTON", "SELECT"].includes(target.tagName));
      if (event.code !== "Space" || typing || event.repeat) return;
      event.preventDefault();
      if (connection === "disconnected") void start();
      else if (session.state === "speaking") interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openThread, start, interrupt, connection, session.state]);
  // A new `models` event from the socket replaces the registry; later HTTP refreshes may override it.
  const socketModels = useRef(session.voiceModels);
  if (session.voiceModels !== socketModels.current) {
    socketModels.current = session.voiceModels;
    if (session.voiceModels.length > 0) setRegistry(session.voiceModels);
  }
  const [{ catalog, error: catalogError }, refresh] = useServerCatalog();
  const providers = catalog?.providers ?? NO_PROVIDERS;
  const [connecting, setConnecting] = useState<ProviderInfo>();
  // The registry (with `locked` models) before any socket is open and after a connect.
  useEffect(() => {
    if (catalog !== undefined) setRegistry(catalog.models);
  }, [catalog]);
  useEffect(() => {
    // Back from an OAuth sign-in: the server redirects to `/#connected=<provider>`.
    const connected = /^#connected=([\w-]+)$/.exec(window.location.hash)?.[1];
    if (connected) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      toast.success(`${connected} connected`);
    }
  }, []);
  const onLockedProvider = useCallback(
    (id: string) => {
      const action = lockedProviderAction(providers, id);
      if (action.kind === "connect") setConnecting(action.provider);
      else toast(action.message);
    },
    [providers],
  );
  const { configure } = session;
  const onPick = useCallback(
    (next: VoiceSelection) => {
      // Saved right away (idle, nothing confirms it until the next start); `voice.configured`
      // overwrites it if the server rejects it. Live: voice/effort via session.update, a model change by hot-swap.
      save(next);
      configure(next);
    },
    [save, configure],
  );
  speaking.current = session.state === "speaking";
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [feed]);

  const connected = session.connection === "connected";
  const previousConnection = useRef(session.connection);
  useEffect(() => {
    if (previousConnection.current !== "disconnected" && session.connection === "disconnected") void window.osmoDesktop?.setEnabled(false);
    previousConnection.current = session.connection;
  }, [session.connection]);
  const { sendWidgetAction, sendBrowserInput, closeBrowser } = session;
  const browserControls = useMemo(() => ({ input: sendBrowserInput, close: closeBrowser }), [sendBrowserInput, closeBrowser]);
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
  const mode = orbMode(session.connection, session.state, pending !== undefined);
  const amber = mode === "awaiting";

  useEffect(() => {
    const labels: Record<typeof mode, string> = {
      connecting: "Connecting",
      idle: "Ready",
      listening: "Listening",
      awaiting: "Your choice",
      thinking: "Thinking",
      speaking: "Speaking",
    };
    const opacity: Record<typeof mode, number> = { connecting: 0.4, idle: 0.6, listening: 1, awaiting: 1, thinking: 0.8, speaking: 1 };
    const fill = theme === "light" ? "#171717" : "#ededed";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="24" fill="${fill}" opacity="${opacity[mode]}"/></svg>`;
    const icon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    const previousTitle = document.title;
    const previousHref = icon?.href;
    document.title = `Osmo · ${labels[mode]}`;
    if (icon) icon.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    return () => {
      document.title = previousTitle;
      if (icon && previousHref) icon.href = previousHref;
    };
  }, [mode, theme]);
  useEffect(() => cues.current?.onAmber(amber ? pending : undefined), [amber, pending]);
  const onFeedChange = useCallback((event: ChangeEvent<HTMLElement>) => {
    const target = event.target;
    if (target instanceof HTMLInputElement && target.type === "radio" && target.closest(".chat-question")) {
      cues.current?.onOptionPick();
    }
  }, []);

  // This thread's runs are cards under the answers that started them; the list is for the others'.
  const shownRuns = visibleRuns(runs, now).filter((run) => run.threadId !== undefined && run.threadId !== threadId);
  const runsList = <RunsList runs={shownRuns} now={now} onReview={(run) => void review(run)} />;

  return (
    <>
      <ThreadSidebar
        threads={threads}
        activeId={threadId}
        onSelect={(id) => void openThread(id)}
        onNew={() => void openThread(undefined)}
        onRename={(id, title) => void renameThread(id, title)}
        onFork={forkThread}
        onDelete={(id) => void deleteThread(id)}
        countMessages={countMessages}
        footer={desktop.available ? <DesktopSupport desktop={desktop} /> : undefined}
      />
      <SidebarInset className="app">
        <header className="topbar">
          <div className="brand">
            {sidebar.state === "collapsed" && <SidebarTrigger aria-label="Expand sidebar" className="osmo-icon-btn" />}
            <strong>{threads.find((t) => t.id === threadId)?.title ?? "Osmo"}</strong>
          </div>
          <div className="topbar-right">
            {(sidebar.state === "collapsed" || sidebar.isMobile) && <DesktopSupport desktop={desktop} />}
            {session.startedAt !== undefined && <Clock since={session.startedAt} />}
            {connected && (
              <button type="button" className="end" onClick={session.end}>
                <span aria-hidden>✕</span> End
              </button>
            )}
          </div>
        </header>

        <WidgetProvider widgets={osmoWidgets} respondToWidget={respondToWidget} disabled={!connected}>
          <BrowserControlsContext.Provider value={browserControls}>
            <main className="canvas chat-root" data-theme={theme} onChange={onFeedChange}>
              {feed.turns.length === 0 ? (
                <AsciiHome />
              ) : (
                <div className="feed">
                  {shownRuns.length > 0 && <div className="osmo-feed-runs">{runsList}</div>}
                  {feed.turns.map((turn) => (
                    <FeedTurn key={turn.key} turn={turn} step={turn.role === "approval" ? runs[turn.runId]?.steps : undefined} onDecide={decide} />
                  ))}
                  <div ref={bottom} />
                </div>
              )}
            </main>
          </BrowserControlsContext.Provider>
        </WidgetProvider>

        <footer className="dock">

            <OrbComposer
              session={session}
              awaitingChoice={pending !== undefined}
              pickerError={catalogError}
              picker={
                pick && registry.length > 0
                  ? {
                      registry,
                      value: pick,
                      memory: pickMemory,
                      onChange: onPick,
                      onPreview: session.previewVoice,
                      onPreviewStop: session.stopPreview,
                      previewing: session.previewing,
                      previewDisabled: session.state !== "idle" && session.state !== "listening",
                      providers,
                      onLockedProvider,
                    }
                  : undefined
              }
            />
        </footer>
      </SidebarInset>
      <ConnectProviderDialog
        provider={connecting}
        onOpenChange={(open) => {
          if (!open) setConnecting(undefined);
        }}
        onConnected={() => {
          setConnecting(undefined);
          refresh();
        }}
      />
      <Toaster position="top-center" theme={theme} />
    </>
  );
}

type AssistantTurn = Extract<Turn, { role: "assistant" }>;

function FeedTurn({ turn, step, onDecide }: { turn: Turn; step: number | undefined; onDecide: (approvalId: string, runId: string, approved: boolean) => Promise<void> }) {
  switch (turn.role) {
    case "user":
      return (
        <div className="turn user">
          <div className="bubble" data-pending={turn.pending || undefined}>
            {turn.text ? (
              <MessageContent parts={[{ type: "text", text: turn.text }]} isUser />
            ) : (
              <span className="heard-dots" aria-label="Transcribing">
                <i />
                <i />
                <i />
              </span>
            )}
          </div>
        </div>
      );
    case "approval":
      return <ApprovalPrompt turn={turn} step={step} onDecide={(approved) => void onDecide(turn.approvalId, turn.runId, approved)} />;
    case "assistant":
      return (
        <div className="turn assistant">
          <span className="avatar" aria-hidden />
          <div className="assistant-body">
            <MessageContent parts={assistantParts(turn)} />
            {/* One element per widget keyed by its server id, so React state follows the
                widget itself and never shifts when text or activity rows are added above. */}
            {turn.widgets.map((widget) => {
              const part = toWidgetPart(widget.id, widget.type, widget.props);
              return part ? <MessageContent key={widget.id} parts={[part]} /> : null;
            })}
          </div>
        </div>
      );
  }
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

/** One assistant turn's spoken line, tool activity rows and agent runs as chat-sdk parts; widgets render separately, keyed by id. */
function assistantParts(turn: AssistantTurn): AgentPart[] {
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
  for (const run of turn.runs) parts.push({ type: "data", name: "subagent", id: run.runId, data: subagent(run) });
  return parts;
}

const SUBAGENT_STATE: Record<RunCard["status"], AgentSubagentState> = {
  running: "running",
  waiting_approval: "running",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

function subagent(run: RunCard): AgentSubagent {
  return {
    id: run.runId,
    title: "Symphony",
    task: run.task,
    state: SUBAGENT_STATE[run.status],
    error: run.status === "failed" ? run.result : undefined,
    // The answer is the voice model's to tell; the card keeps it under View activity, after the steps.
    messages: [...run.phases, ...(run.status === "completed" && run.result ? [run.result] : [])].map((text, i) => ({
      id: `${run.runId}-${i}`,
      role: "assistant",
      parts: [{ type: "text", text }],
    })),
  };
}
