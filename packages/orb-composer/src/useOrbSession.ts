import { useCallback, useEffect, useRef, useState } from "react";
import { MicCapture, PcmPlayer } from "./audio";
import { OrbClient } from "./client";
import { AUDIO_SAMPLE_RATE, BARGE_IN, type OrbState, type ServerEvent } from "./protocol";

export type OrbConnection = "disconnected" | "connecting" | "connected";

export interface OrbSessionOptions {
  url: string;
  voice?: string;
  onEvent?: (event: ServerEvent) => void;
}

export interface OrbSession {
  connection: OrbConnection;
  /** What the orb shows: `speaking` while assistant audio is still playing. */
  state: OrbState;
  detail: string | undefined;
  error: string | undefined;
  startedAt: number | undefined;
  start: () => Promise<void>;
  end: () => void;
  interrupt: () => void;
  sendWidgetAction: (id: string, action: string, value?: string) => void;
}

interface Runtime {
  client: OrbClient;
  context: AudioContext;
  mic: MicCapture;
  player: PcmPlayer;
}

export function useOrbSession({ url, voice, onEvent }: OrbSessionOptions): OrbSession {
  const [connection, setConnection] = useState<OrbConnection>("disconnected");
  const [serverState, setServerState] = useState<OrbState>("idle");
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
    const client = new OrbClient(url, {
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
  };
}
