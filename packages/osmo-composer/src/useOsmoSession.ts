import { useCallback, useEffect, useRef, useState } from "react";
import { MicCapture, PcmPlayer } from "./audio";
import { OsmoClient } from "./client";
import { AUDIO_SAMPLE_RATE, BARGE_IN, type OsmoState, type ServerEvent } from "./protocol";

export type OsmoConnection = "disconnected" | "connecting" | "connected";

export interface OsmoSessionOptions {
  url: string;
  voice?: string;
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
  /** Assistant output level 0..1, read straight off the playback analyser; poll it from rAF. */
  outputLevel: () => number;
}

interface Runtime {
  client: OsmoClient;
  context: AudioContext;
  mic: MicCapture;
  player: PcmPlayer;
}

export function useOsmoSession({ url, voice, onEvent }: OsmoSessionOptions): OsmoSession {
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
    current.mic.stop();
    current.player.flush();
    current.client.close();
    void current.context.close();
  }, []);

  const start = useCallback(async () => {
    if (runtime.current) return;
    setError(undefined);
    setConnection("connecting");
    const context = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
    const player = new PcmPlayer(context, () => setPlaying(false));
    const client = new OsmoClient(url, {
      onAudio: (frame) => {
        player.enqueue(frame);
        setPlaying(true);
      },
      onEvent: (event) => {
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
    runtime.current = { client, context, mic, player };
    try {
      // Mic and socket start in parallel, as the xAI latency guide recommends.
      await Promise.all([client.connect(), context.resume()]);
      client.send({ type: "session.start", voice });
      await mic.start((frame) => client.sendAudio(frame));
      setConnection("connected");
      setStartedAt(Date.now());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      teardown();
      setConnection("disconnected");
    }
  }, [teardown, url, voice]);

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

  const outputLevel = useCallback(() => runtime.current?.player.level() ?? 0, []);

  useEffect(() => teardown, [teardown]);

  return {
    connection,
    state: playing ? "speaking" : serverState,
    detail,
    error,
    startedAt,
    start,
    end,
    interrupt,
    sendWidgetAction,
    outputLevel,
  };
}
