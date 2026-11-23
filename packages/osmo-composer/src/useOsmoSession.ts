import { useCallback, useEffect, useRef, useState } from "react";
import { MicCapture, PcmPlayer } from "./audio";
import { OsmoClient } from "./client";
import { EchoGuard } from "./echoGuard";
import { SileroVad } from "./sileroVad";
import { AUDIO_SAMPLE_RATE, BARGE_IN, type BrowserInput, type Effort, type MusicState, type OsmoState, type ServerEvent, type VoiceModel, type VoiceSelection } from "./protocol";

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
  /** Take the agent browser widget `id`'s browser over from the agent (`user`), or hand it back. */
  takeOverBrowser: (id: string, user: boolean) => void;
  /** Tell the server what the app's music player is doing (no-op while disconnected). */
  reportMusic: (state: MusicState) => void;
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
  /** Mic level 0..1 while the user is talking; 0 until capture has started. Poll it from rAF. */
  inputLevel: () => number;
}

interface Runtime {
  client: OsmoClient;
  context: AudioContext;
  mic: MicCapture;
  silero: SileroVad;
  player: PcmPlayer;
  preview: PcmPlayer;
}

export function useOsmoSession({ url, voice, model, effort, threadId, onEvent }: OsmoSessionOptions): OsmoSession {
  const [voiceModels, setVoiceModels] = useState<VoiceModel[]>([]);
  const [selection, setSelection] = useState<VoiceSelection | undefined>();
  const [previewing, setPreviewing] = useState<{ model: string; voice: string } | undefined>();
  const previewRequest = useRef<{ id: string; model: string; voice: string } | undefined>();
  const previewCount = useRef(0);
  const speakingRef = useRef(false);
  // Whether the music player is playing (it reports through reportMusic): the echo guard keeps it from the server's VAD.
  const musicPlaying = useRef(false);
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
  const eventSink = useRef(onEvent);
  eventSink.current = onEvent;

  const teardown = useCallback(() => {
    const current = runtime.current;
    runtime.current = null;
    if (!current) return;
    current.silero.stop();
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
    const silero = new SileroVad();
    runtime.current = { client, context, mic, silero, player, preview };
    try {
      // Mic and socket start in parallel, as the xAI latency guide recommends.
      await Promise.all([client.connect(), context.resume()]);
      client.send({ type: "session.start", voice, model, effort, threadId });
      // A preview plays on the speakers: keep it out of the mic so it is not heard as the user. Osmo's own voice is
      // held back by the echo guard, which also cuts Osmo off here the moment the user talks over it.
      const guard = new EchoGuard(AUDIO_SAMPLE_RATE);
      const debugAudio = new URLSearchParams(window.location.search).has("debugAudio");
      let captured = 0;
      let forwarded = 0;
      let guarded = 0;
      let bargeIns = 0;
      await mic.start((frame) => {
        if (previewingRef.current) return;
        const now = performance.now();
        const step = guard.push(frame, player.playing, player.rms(), now, musicPlaying.current, silero.ready ? silero.speaking(now) : undefined);
        captured++;
        forwarded += step.send.length;
        if (step.send.length === 0) guarded++;
        if (step.bargeIn) bargeIns++;
        if (debugAudio && captured % 50 === 0) {
          console.info("[osmo audio] web capture/forwarded/held/bargeIns", { captured, forwarded, guarded, bargeIns, playing: player.playing, music: musicPlaying.current });
        }
        if (step.bargeIn) {
          player.cut();
          client.send({ type: "interrupt" });
        }
        for (const held of step.send) client.sendAudio(held);
      });
      const stream = mic.mediaStream;
      if (stream) void silero.start(stream);
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

  const takeOverBrowser = useCallback((id: string, user: boolean) => {
    runtime.current?.client.send({ type: "browser.takeover", id, user });
  }, []);

  const reportMusic = useCallback((state: MusicState) => {
    musicPlaying.current = state.playing;
    runtime.current?.client.send(state);
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
  const inputLevel = useCallback(() => runtime.current?.mic.level() ?? 0, []);

  useEffect(() => teardown, [teardown]);

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
    takeOverBrowser,
    reportMusic,
    voiceModels,
    selection,
    configure,
    previewVoice,
    stopPreview,
    previewing,
    outputLevel,
    inputLevel,
  };
}

function base64ToBuffer(data: string): ArrayBuffer {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
