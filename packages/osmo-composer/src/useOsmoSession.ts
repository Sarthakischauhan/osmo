import { useCallback, useEffect, useRef, useState } from "react";
import { MicCapture, PcmPlayer } from "./audio";
import { OsmoClient } from "./client";
import { AUDIO_SAMPLE_RATE, BARGE_IN, type BrowserInput, type Effort, type OsmoState, type ServerEvent, type VoiceModel, type VoiceSelection } from "./protocol";

export type OsmoConnection = "disconnected" | "connecting" | "connected";

export interface OsmoSessionOptions {
  url: string;
  voice?: string;
  /** Voice model and reasoning effort to start with (the picker's value). */
  model?: string;
  effort?: Effort;
  /** The stored thread to continue when the session starts; a new one when unset. */
  threadId?: string;
  onEvent?: (event: ServerEvent) => void;
  desktop?: { enabled: boolean; execute: (action: Record<string, unknown>) => Promise<Record<string, unknown>> };
}

export interface OsmoSession {
  connection: OsmoConnection;
  /** What the orb shows: `speaking` while assistant audio is still playing. */
  state: OsmoState;
  detail: string | undefined;
  error: string | undefined;
  startedAt: number | undefined;
  start: () => Promise<void>;
  end: () => void;
  interrupt: () => void;
  sendWidgetAction: (id: string, action: string, value?: string) => void;
  /** The user's click, scroll or key on the agent browser widget `id` (the server plays it into the page). */
  sendBrowserInput: (id: string, input: BrowserInput) => void;
  /** Close the agent browser widget `id`'s browser. */
  closeBrowser: (id: string) => void;
  /** Symphony's voice model registry (`models` event); empty until connected. */
  voiceModels: VoiceModel[];
  /** The session's model/voice/effort as Symphony last confirmed it (picker commit or `set_voice`). */
  selection: VoiceSelection | undefined;
  /** Send `session.configure` for a picker commit. */
  configure: (selection: VoiceSelection) => void;
  /** Ask Symphony for a ~2 s sample of a voice and play it; replaces any preview playing. */
  previewVoice: (pick: { model: string; voice: string }) => void;
  stopPreview: () => void;
  /** The voice whose preview is playing. */
  previewing: { model: string; voice: string } | undefined;
  /** Assistant output level 0..1, read straight off the playback analyser; poll it from rAF. */
  outputLevel: () => number;
}

interface Runtime {
  client: OsmoClient;
  context: AudioContext;
  mic: MicCapture;
  player: PcmPlayer;
  preview: PcmPlayer;
}

export function useOsmoSession({ url, voice, model, effort, threadId, onEvent, desktop }: OsmoSessionOptions): OsmoSession {
  const [voiceModels, setVoiceModels] = useState<VoiceModel[]>([]);
  const [selection, setSelection] = useState<VoiceSelection | undefined>();
  const [previewing, setPreviewing] = useState<{ model: string; voice: string } | undefined>();
  const previewRequest = useRef<{ id: string; model: string; voice: string } | undefined>();
  const previewCount = useRef(0);
  const speakingRef = useRef(false);
  // Previews only while the session is idle or listening (the server enforces the same).
  const previewStateRef = useRef(true);
  const previewingRef = useRef(false);
  const [connection, setConnection] = useState<OsmoConnection>("disconnected");
  const [serverState, setServerState] = useState<OsmoState>("idle");
  const [detail, setDetail] = useState<string | undefined>();
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [startedAt, setStartedAt] = useState<number | undefined>();
  const runtime = useRef<Runtime | null>(null);
  const desktopRef = useRef(desktop);
  desktopRef.current = desktop;
  const eventSink = useRef(onEvent);
  eventSink.current = onEvent;

  const teardown = useCallback(() => {
    const current = runtime.current;
    runtime.current = null;
    if (!current) return;
    current.mic.stop();
    current.player.flush();
    current.preview.flush();
    current.client.close();
    void current.context.close();
  }, []);

  const start = useCallback(async () => {
    if (runtime.current) return;
    setError(undefined);
    setConnection("connecting");
    const context = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
    const player = new PcmPlayer(context, () => setPlaying(false));
    const preview = new PcmPlayer(context, () => setPreviewing(undefined));
    const client = new OsmoClient(url, {
      onAudio: (frame) => {
        player.enqueue(frame);
        setPlaying(true);
      },
      onEvent: (event) => {
        if (event.type === "desktop.request") {
          if (runtime.current?.client !== client) return;
          const native = desktopRef.current;
          void (async () => {
            try {
              if (!native?.enabled) throw new Error("Computer support is disabled");
              const result = await native.execute(event.action);
              if (runtime.current?.client === client) client.send({ type: "desktop.result", requestId: event.requestId, result });
            } catch (error) {
              if (runtime.current?.client === client) client.send({ type: "desktop.result", requestId: event.requestId,
                error: (error instanceof Error ? error.message : String(error)).slice(0, 2000) });
            }
          })();
          return;
        }
        if (event.type === "models") {
          setVoiceModels(event.models);
          setSelection(event.selection);
        } else if (event.type === "voice.configured") {
          setSelection(event.selection);
        } else if (event.type === "voice.preview") {
          const wanted = previewRequest.current;
          if (wanted?.id === event.requestId && event.audio !== undefined && !speakingRef.current) {
            preview.flush();
            preview.enqueue(base64ToBuffer(event.audio));
            setPreviewing({ model: wanted.model, voice: wanted.voice });
          }
        }
        if (event.type === "state") {
          if (event.detail === BARGE_IN) player.flush();
          setServerState(event.state);
          setDetail(event.detail === BARGE_IN ? undefined : event.detail);
        }
        eventSink.current?.(event);
      },
      onClose: () => {
        teardown();
        setConnection("disconnected");
        setServerState("idle");
        setStartedAt(undefined);
      },
    });
    const mic = new MicCapture(context);
    runtime.current = { client, context, mic, player, preview };
    try {
      // Mic and socket start in parallel, as the xAI latency guide recommends.
      await Promise.all([client.connect(), context.resume()]);
      client.send({ type: "session.start", voice, model, effort, threadId });
      if (desktopRef.current?.enabled) client.send({ type: "desktop.configure", enabled: true });
      // A preview plays on the speakers: keep it out of the mic so it is not heard as the user.
      await mic.start((frame) => {
        if (!previewingRef.current) client.sendAudio(frame);
      });
      setConnection("connected");
      setStartedAt(Date.now());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      teardown();
      setConnection("disconnected");
    }
  }, [teardown, url, voice, model, effort, threadId]);

  const end = useCallback(() => {
    runtime.current?.client.send({ type: "session.end" });
    teardown();
    setConnection("disconnected");
    setServerState("idle");
    setStartedAt(undefined);
  }, [teardown]);

  const interrupt = useCallback(() => {
    const current = runtime.current;
    if (!current) return;
    current.player.flush();
    current.client.send({ type: "interrupt" });
  }, []);

  const sendWidgetAction = useCallback((id: string, action: string, value?: string) => {
    runtime.current?.player.flush();
    runtime.current?.client.send({ type: "widget.action", id, action, value });
  }, []);

  const sendBrowserInput = useCallback((id: string, input: BrowserInput) => {
    runtime.current?.client.send({ ...input, id });
  }, []);

  const closeBrowser = useCallback((id: string) => {
    runtime.current?.client.send({ type: "browser.close", id });
  }, []);

  const configure = useCallback((next: VoiceSelection) => {
    runtime.current?.client.send({ type: "session.configure", ...next });
  }, []);

  const stopPreview = useCallback(() => {
    previewRequest.current = undefined;
    runtime.current?.preview.flush();
  }, []);

  const previewVoice = useCallback((pick: { model: string; voice: string }) => {
    const current = runtime.current;
    if (!current || !previewStateRef.current) return;
    current.preview.flush();
    previewCount.current += 1;
    const id = `p${previewCount.current}`;
    previewRequest.current = { id, ...pick };
    current.client.send({ type: "voice.preview", requestId: id, ...pick });
  }, []);

  const outputLevel = useCallback(() => runtime.current?.player.level() ?? 0, []);

  useEffect(() => teardown, [teardown]);
  useEffect(() => { runtime.current?.client.send({ type: "desktop.configure", enabled: desktop?.enabled ?? false }); }, [desktop?.enabled, connection]);

  const shownState: OsmoState = playing ? "speaking" : serverState;
  speakingRef.current = shownState === "speaking";
  previewStateRef.current = shownState === "idle" || shownState === "listening";
  previewingRef.current = previewing !== undefined;
  // Never talk over the agent: a preview stops as soon as it speaks.
  useEffect(() => {
    if (shownState === "speaking") stopPreview();
  }, [shownState, stopPreview]);

  return {
    connection,
    state: shownState,
    detail,
    error,
    startedAt,
    start,
    end,
    interrupt,
    sendWidgetAction,
    sendBrowserInput,
    closeBrowser,
    voiceModels,
    selection,
    configure,
    previewVoice,
    stopPreview,
    previewing,
    outputLevel,
  };
}

function base64ToBuffer(data: string): ArrayBuffer {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
