import { osmoWidgets, toWidgetPart } from "@osmo/osmo-widgets";
import { MessageContent, WidgetProvider, type WidgetResponse } from "@sarchauhan/chat";
import "@sarchauhan/chat/styles.css";
import type { AgentPart } from "@sarchauhan/protocol";
import {
  OrbComposer,
  type ServerEvent,
  type VoiceModel,
  type VoiceSelection,
  orbMode,
  serverEventSchema,
  sameOriginSocketUrl,
  useOsmoSession,
  usePersistedPick,
} from "@osmo/osmo-composer";
import "@osmo/osmo-composer/radial-picker.css";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { ConnectProviderDialog, type ProviderInfo, providersResponseSchema } from "./ConnectProvider";
import { Cues } from "./cues";
import { type ChangeEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type Feed, type Turn, emptyFeed, pendingChoice, reduceFeed } from "./feed";

export function App() {
  const [feed, dispatch] = useReducer((state: Feed, event: ServerEvent) => reduceFeed(state, event), emptyFeed);
  const speaking = useRef(false);
  const cues = useRef<Cues | null>(null);
  cues.current ??= new Cues(() => speaking.current);
  const pickSetters = useRef<{ save: (next: VoiceSelection) => void; choose: (next: VoiceSelection) => void }>();
  const onEvent = useCallback((event: ServerEvent) => {
    dispatch(event);
    cues.current?.onServerEvent(event);
    // The server confirmed a pick (session start, picker, or `set_voice`): only now is it saved.
    // On error the picker snaps back to what is still in effect, and nothing is saved.
    if (event.type === "voice.configured") {
      if (event.error === undefined) pickSetters.current?.save(event.selection);
      else pickSetters.current?.choose(event.selection);
    }
  }, []);
  const [registry, setRegistry] = useState<VoiceModel[]>([]);
  const [pick, save, choose] = usePersistedPick(registry);
  pickSetters.current = { save, choose };
  const session = useOsmoSession({
    url: sameOriginSocketUrl(window.location),
    model: pick?.model,
    voice: pick?.voice,
    effort: pick?.effort,
    onEvent,
  });
  // A new `models` event from the socket replaces the registry; later HTTP refreshes may override it.
  const socketModels = useRef(session.voiceModels);
  if (session.voiceModels !== socketModels.current) {
    socketModels.current = session.voiceModels;
    if (session.voiceModels.length > 0) setRegistry(session.voiceModels);
  }
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [connecting, setConnecting] = useState<ProviderInfo>();
  // The registry (with `locked` models) and the providers, before any socket is open and after a connect.
  const refresh = useCallback((signal?: AbortSignal) => {
    fetch("/voice-models", { signal })
      .then((response) => (response.ok ? response.json() : undefined))
      .then((body: unknown) => {
        const parsed = serverEventSchema.safeParse(body);
        if (parsed.success && parsed.data.type === "models") setRegistry(parsed.data.models);
      })
      .catch(() => undefined);
    fetch("/providers", { signal })
      .then((response) => (response.ok ? response.json() : undefined))
      .then((body: unknown) => {
        const parsed = providersResponseSchema.safeParse(body);
        if (parsed.success) setProviders(parsed.data.providers);
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    // Back from an OAuth sign-in: the server redirects to `/#connected=<provider>`.
    const connected = /^#connected=([\w-]+)$/.exec(window.location.hash)?.[1];
    if (connected) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      toast.success(`${connected} connected`);
    }
    return () => controller.abort();
  }, [refresh]);
  const onLockedProvider = useCallback(
    (id: string) => {
      const provider = providers.find((p) => p.id === id);
      if (provider) setConnecting(provider);
      else toast(`Osmo cannot connect ${id} yet`);
    },
    [providers],
  );
  const { configure } = session;
  const onPick = useCallback(
    (next: VoiceSelection) => {
      // Shown right away; saved once `voice.configured` confirms it (on session start if idle).
      // Live: voice/effort via session.update, a model change by hot-swap.
      choose(next);
      configure(next);
    },
    [choose, configure],
  );
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
    const colors: Record<typeof mode, [string, string, string]> = {
      connecting: ["#d6dae4", "#8c94a8", "#464c60"],
      idle: ["#e2e6ff", "#8c96f0", "#3e3aaa"],
      listening: ["#e6ecff", "#8192f8", "#4338ca"],
      awaiting: ["#ffecbe", "#fbbf24", "#b45309"],
      thinking: ["#fff", "#ddd6fe", "#a78bfa"],
      speaking: ["#e0e7ff", "#818cf8", "#4c1d95"],
    };
    const [light, mid, deep] = colors[mode];
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><radialGradient id="g" cx="35%" cy="28%" r="70%"><stop stop-color="${light}"/><stop offset=".55" stop-color="${mid}"/><stop offset="1" stop-color="${deep}"/></radialGradient></defs><circle cx="32" cy="32" r="25" fill="${mid}" opacity=".3"/><circle cx="32" cy="32" r="21" fill="url(#g)"/></svg>`;
    const icon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    const previousTitle = document.title;
    const previousHref = icon?.href;
    document.title = `Osmo · ${labels[mode]}`;
    if (icon) icon.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    return () => {
      document.title = previousTitle;
      if (icon && previousHref) icon.href = previousHref;
    };
  }, [mode]);
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
        <OrbComposer
          session={session}
          awaitingChoice={pending !== undefined}
          picker={
            pick && registry.length > 0
              ? {
                  registry,
                  value: pick,
                  onChange: onPick,
                  onPreview: session.previewVoice,
                  onPreviewStop: session.stopPreview,
                  previewing: session.previewing,
                  previewDisabled: session.state !== "idle" && session.state !== "listening",
                  onLockedProvider,
                }
              : undefined
          }
        />
      </footer>
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
      <Toaster position="top-center" />
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
