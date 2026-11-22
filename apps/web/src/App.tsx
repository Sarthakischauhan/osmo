import { useDesktopSupport } from "./desktopSupport";
import { useAppearance, type Theme } from "./appearance";
import type { WidgetResponse } from "@sarchauhan/chat";
import "@sarchauhan/chat/styles.css";
import {
  OrbComposer,
  type ServerEvent,
  type Thread,
  type UsageRecord,
  type VoiceModel,
  type VoiceSelection,
  orbMode,
  sameOriginSocketUrl,
  usePersistedPick,
} from "@osmo/osmo-composer";
import "@osmo/osmo-composer/radial-picker.css";
import { Toaster } from "@/components/ui/sonner";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThreadSidebar } from "@/components/ThreadSidebar";
import { AgentWorkbench } from "@/components/AgentWorkbench";
import { RunsList } from "@/components/home/RunsList";
import { VoiceField } from "@/components/home/VoiceField";
import { type ApprovalRequest, type RunView, type Runs, decideApproval, reduceRuns, visibleRuns } from "@/lib/runs";
import { threadApi } from "@/lib/threads";
import { toast } from "sonner";
import { type ProviderInfo, lockedProviderAction } from "./ConnectProvider";
import { useServerCatalog } from "./serverCatalog";
import { useAppView, type AppView } from "./app/views";
import { useNow } from "./app/Clock";
import { useThreadActions } from "./app/useThreadActions";
import { useRunsPolling } from "./app/useRunsPolling";
import { useKeyboardShortcuts } from "./app/useKeyboardShortcuts";
import { useTabStatus } from "./app/useTabStatus";
import { Topbar } from "./app/Topbar";
import { ChatFeed } from "./app/ChatFeed";
import { ProviderDialogs, useProviderDialogs } from "./app/ProviderDialogs";
import { useMusicReporting } from "@/music/useMusicReporting";
import { Cues } from "./cues";
import { type ChangeEvent, type ReactNode, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useSession } from "./session";
import { UsageDialog } from "@/components/UsageDialog";
import { NotesView } from "@/components/NotesView";
import type { SidebarPlace } from "@/components/ThreadSidebar";
import { CalendarDays, ListTodo, NotebookPen } from "lucide-react";
import { CalendarView } from "@/components/CalendarView";
import { MusicMini } from "@/components/MusicMini";
import { musicPlayer } from "@/music/player";
import { agentActivity } from "./compact";
import { liveThought, orbBubbleVisible } from "./orbActivity";
import { CompactAgentActivity } from "./CompactAgentActivity";
import { type Feed, conversationTitle, emptyFeed, feedFromMessages, pendingChoice, reduceFeed } from "./feed";

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

function Osmo({ theme, setTheme }: { theme: Theme; setTheme: (theme: Theme) => void }) {
  const desktop = useDesktopSupport();
  const [feed, dispatchFeed] = useReducer(feedReducer, emptyFeed);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadId, setThreadId] = useState<string>();
  // The automation the calendar opens on (Tasks links each automation task to it).
  const [calendarFocus, setCalendarFocus] = useState<string>();
  const [runs, setRuns] = useState<Runs>({});
  const approvals = useRef<ApprovalRequest[]>([]);
  const now = useNow();
  const speaking = useRef(false);
  const cues = useRef<Cues | null>(null);
  cues.current ??= new Cues(() => speaking.current);
  const savePick = useRef<(next: VoiceSelection) => void>();
  const [liveUsage, setLiveUsage] = useState<UsageRecord>();
  const [usageOpen, setUsageOpen] = useState(false);
  // The main pane: the conversation, or one of Osmo's other screens (the orb stays in the dock on all of them).
  const [view, setView] = useAppView();
  const onEvent = useCallback((event: ServerEvent) => {
    dispatchFeed({ kind: "event", event });
    setRuns((previous) => reduceRuns(previous, event, Date.now()));
    if (event.type === "thread") {
      setThreadId(event.thread.id);
      setThreads((previous) => [event.thread, ...previous.filter((t) => t.id !== event.thread.id)]);
    }
    cues.current?.onServerEvent(event);
    if (event.type === "usage") setLiveUsage(event.usage);
    if (event.type === "voice.error") toast.error(event.message);
    if (event.type === "music.command") void musicPlayer.command(event);
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
  const { end, reportMusic } = session;
  useMusicReporting(reportMusic, session.connection === "connected", session.state === "speaking");
  // In the Electron app the orb window starts calls too: it continues whichever thread this window has open.
  useEffect(() => {
    window.osmoDesktop?.session.openThread(threadId).catch(() => toast.error("Could not share the open thread with the orb"));
  }, [threadId]);
  useEffect(() => {
    api.list().then(setThreads, () => toast.error("Could not load threads"));
  }, []);
  const openThread = useCallback(
    async (id: string | undefined) => {
      end();
      void window.osmoDesktop?.setEnabled(false);
      setThreadId(id);
      setView("chat");
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
  const { renameThread, forkThread, deleteThread, countMessages } = useThreadActions(threadId, setThreads, openThread);
  // The socket only echoes approval.resolved while a session is live, so record a decision the server accepted here.
  const decide = useCallback(async (approvalId: string, runId: string, approved: boolean) => {
    if (await decideApproval(approvalId, approved)) dispatchFeed({ kind: "event", event: { type: "approval.resolved", approvalId, runId, approved } });
    else toast.error("That approval is no longer pending");
  }, []);
  useRunsPolling(setRuns, approvals);
  const review = useCallback(
    async (run: RunView) => {
      if (run.threadId !== undefined && run.threadId !== threadId) await openThread(run.threadId);
      window.setTimeout(() => document.getElementById(`approval-${run.approvalId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
    },
    [openThread, threadId],
  );
  const { start, interrupt } = session;
  const newThread = useCallback(() => void openThread(undefined), [openThread]);
  const startCall = useCallback(() => void start(), [start]);
  useKeyboardShortcuts({ newThread, start: startCall, interrupt, disconnected: session.connection === "disconnected", speaking: session.state === "speaking" });
  // A new `models` event from the socket replaces the registry; later HTTP refreshes may override it.
  const socketModels = useRef(session.voiceModels);
  if (session.voiceModels !== socketModels.current) {
    socketModels.current = session.voiceModels;
    if (session.voiceModels.length > 0) setRegistry(session.voiceModels);
  }
  const [{ catalog, error: catalogError }, refresh] = useServerCatalog();
  const providers = catalog?.providers ?? NO_PROVIDERS;
  const dialogs = useProviderDialogs();
  const { setConnecting, setAgentModelOpen } = dialogs;
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
  const feedScrollRef = useRef<HTMLElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [feed]);

  const connected = session.connection === "connected";
  const previousConnection = useRef(session.connection);
  useEffect(() => {
    if (previousConnection.current !== "disconnected" && session.connection === "disconnected") void window.osmoDesktop?.setEnabled(false);
    previousConnection.current = session.connection;
  }, [session.connection]);
  const { sendWidgetAction, sendBrowserInput, closeBrowser, takeOverBrowser } = session;
  const browserControls = useMemo(
    () => ({ input: sendBrowserInput, close: closeBrowser, takeOver: takeOverBrowser }),
    [sendBrowserInput, closeBrowser, takeOverBrowser],
  );
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

  useTabStatus(mode, theme);
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
  const orbActivity = agentActivity(runs, feed.turns, threadId, now);
  // Reasoning remains in the transcript after a turn ends. The bubble above the orb is the
  // live "working" indicator, same for every provider: only while this turn is thinking or a
  // tool/agent run is still going. Idle, listening and speaking stay the orb alone.
  const currentThought = liveThought(feed.turns);
  const showThought = currentThought !== undefined && (session.state === "thinking" || session.state === "working");
  const showOrbBubble = orbBubbleVisible(session.state, orbActivity, showThought);
  // The welcome screen holds the orb at the centre over the voice field; the first turn docks it.
  const workbenchOpen = view === "tasks";
  const home = view === "chat" && feed.turns.length === 0;

  const place = (id: Exclude<AppView, "chat">, label: string, icon: ReactNode): SidebarPlace => ({
    id,
    label,
    icon,
    active: view === id,
    onOpen: () => setView(id),
  });
  // Tasks first, the calendar directly below it (its automations run as tasks), then the notes.
  const places = [
    place("tasks", "Tasks", <ListTodo size={16} aria-hidden />),
    place("calendar", "Calendar", <CalendarDays size={16} aria-hidden />),
    place("notes", "Notes", <NotebookPen size={16} aria-hidden />),
  ];
  const openAutomation = (id: string) => {
    setCalendarFocus(id);
    setView("calendar");
  };
  const screen =
    view === "notes" ? <NotesView /> : view === "calendar" ? <CalendarView focus={calendarFocus} onOpenTask={(id) => void openThread(id)} /> : undefined;

  return (
    <>
      <ThreadSidebar
        threads={threads}
        activeId={view === "chat" ? threadId : undefined}
        onSelect={(id) => void openThread(id)}
        onNew={() => void openThread(undefined)}
        onRename={(id, title) => void renameThread(id, title)}
        onFork={forkThread}
        onDelete={(id) => void deleteThread(id)}
        countMessages={countMessages}
        onOpenSettings={() => setAgentModelOpen(true)}
        onOpenUsage={() => setUsageOpen(true)}
        places={places}
        player={(collapsed) => <MusicMini player={musicPlayer} collapsed={collapsed} />}
      />
      <SidebarInset className="app" data-home={home}>
        {home && <VoiceField mode={mode} inputLevel={session.inputLevel} outputLevel={session.outputLevel} />}
        {screen !== undefined ? <div className="osmo-screen">{screen}</div> : <>
        <Topbar
          title={workbenchOpen ? "Tasks" : conversationTitle(feed.turns) ?? threads.find((t) => t.id === threadId)?.title ?? "Osmo"}
          startedAt={session.startedAt}
          connected={connected}
          onEnd={session.end}
        />

        {workbenchOpen ? <AgentWorkbench onOpenThread={(id) => void openThread(id)} onOpenSettings={() => setAgentModelOpen(true)} onOpenAutomation={openAutomation} /> : <>
        <ChatFeed
          feed={feed}
          runs={runs}
          runsList={shownRuns.length > 0 ? runsList : undefined}
          theme={theme}
          connected={connected}
          browserControls={browserControls}
          respondToWidget={respondToWidget}
          onDecide={decide}
          onChange={onFeedChange}
          scrollRef={feedScrollRef}
          bottomRef={bottom}
        />
        </>}

        <footer className="dock">

            <OrbComposer
              session={session}
              awaitingChoice={pending !== undefined}
              activity={showOrbBubble ? (
                <section aria-label="Orb activity">
                  {showThought && <div className="osmo-compact-thinking"><strong>Thinking</strong><p>{currentThought.reasoning}</p></div>}
                  <CompactAgentActivity activity={orbActivity} runs={runs} onDecide={decide} />
                </section>
              ) : undefined}
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
        </>}
      </SidebarInset>
      <ProviderDialogs dialogs={dialogs} providers={providers} refresh={refresh} desktop={desktop} />
      <UsageDialog open={usageOpen} onOpenChange={setUsageOpen} threadId={threadId} live={liveUsage} />
      <Toaster position="top-center" theme={theme} />
    </>
  );
}
