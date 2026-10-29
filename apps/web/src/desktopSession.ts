import { type OsmoSession, type OsmoSessionOptions, serverEventSchema, type ServerEvent } from "@osmo/osmo-composer";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SessionBridge, SessionSnapshot } from "./desktopBridge";

/** What a window shows before main has answered. */
export const NO_SESSION: SessionSnapshot = {
  connection: "disconnected",
  state: "idle",
  detail: undefined,
  error: undefined,
  startedAt: undefined,
  thread: undefined,
  selection: undefined,
  models: [],
};

/** A server event as main forwarded it: the same schema the browser's socket applies, and anything else is dropped. */
export function parseServerEvent(raw: unknown): ServerEvent | undefined {
  const parsed = serverEventSchema.safeParse(raw);
  return parsed.success ? parsed.data : undefined;
}

/**
 * The session a window shows when Electron's main process owns it: no microphone, speakers or socket here, only the
 * snapshot main keeps and the events it forwards. Both windows use it, so what one starts the other sees and can stop.
 */
export function createDesktopSession(bridge: SessionBridge): (options: OsmoSessionOptions) => OsmoSession {
  return function useDesktopSession({ voice, model, effort, threadId, onEvent }) {
    const [snapshot, setSnapshot] = useState<SessionSnapshot>(NO_SESSION);
    const [failure, setFailure] = useState<string>();
    const sink = useRef(onEvent);
    sink.current = onEvent;

    useEffect(() => {
      let active = true;
      let live = false;
      const dropStatus = bridge.onStatus((next) => {
        live = true;
        setSnapshot(next);
      });
      const dropEvent = bridge.onEvent((raw) => {
        const event = parseServerEvent(raw);
        if (event) sink.current?.(event);
      });
      void bridge.snapshot().then(
        (next) => {
          if (!active) return;
          // A status that arrived first is newer than this answer.
          if (!live) setSnapshot(next);
          // This window may open after the session's thread was announced.
          if (next.thread) sink.current?.({ type: "thread", thread: next.thread });
        },
        (cause) => setFailure(cause instanceof Error ? cause.message : String(cause)),
      );
      return () => {
        active = false;
        dropStatus();
        dropEvent();
      };
    }, []);

    const report = useCallback((request: Promise<unknown>) => {
      setFailure(undefined);
      request.catch((cause) => setFailure(cause instanceof Error ? cause.message : String(cause)));
    }, []);
    const start = useCallback(async () => {
      setFailure(undefined);
      try {
        await bridge.start({ voice, model, effort, threadId });
      } catch (cause) {
        setFailure(cause instanceof Error ? cause.message : String(cause));
      }
    }, [voice, model, effort, threadId]);
    const end = useCallback(() => report(bridge.end()), [report]);
    const interrupt = useCallback(() => report(bridge.interrupt()), [report]);
    const sendWidgetAction = useCallback((id: string, action: string, value?: string) => report(bridge.send({ type: "widget.action", id, action, value })), [report]);
    const sendBrowserInput = useCallback((id: string, input: Parameters<OsmoSession["sendBrowserInput"]>[1]) => report(bridge.send({ ...input, id })), [report]);
    const closeBrowser = useCallback((id: string) => report(bridge.send({ type: "browser.close", id })), [report]);
    const configure = useCallback((selection: Parameters<OsmoSession["configure"]>[0]) => report(bridge.send({ type: "session.configure", ...selection })), [report]);
    // The call's audio plays in main's audio renderer: voice previews and the output level are read off an in-page
    // player in the browser and do not cross the contract.
    const silent = useCallback(() => undefined, []);
    const level = useCallback(() => 0, []);

    return useMemo(
      () => ({
        connection: snapshot.connection,
        state: snapshot.state,
        detail: snapshot.detail,
        error: snapshot.error ?? failure,
        startedAt: snapshot.startedAt,
        start,
        end,
        interrupt,
        sendWidgetAction,
        sendBrowserInput,
        closeBrowser,
        voiceModels: snapshot.models,
        selection: snapshot.selection,
        configure,
        previewVoice: silent,
        stopPreview: silent,
        previewing: undefined,
        outputLevel: level,
      }),
      [snapshot, failure, start, end, interrupt, sendWidgetAction, sendBrowserInput, closeBrowser, configure, silent, level],
    );
  };
}
