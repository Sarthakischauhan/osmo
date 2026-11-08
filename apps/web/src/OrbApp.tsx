import { orbMode, readSavedPick, type ServerEvent } from "@osmo/osmo-composer";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useAppearance } from "./appearance";
import { agentActivity } from "./compact";
import type { BoxPlacement, DesktopBridge } from "./desktopBridge";
import { emptyFeed } from "./feed";
import { FloatingOrb } from "./FloatingOrb";
import { COMPLETED_LINGER_MS, type Runs, decideApproval, fetchRuns, mergeListed, reduceRuns } from "@/lib/runs";
import { reduceOrbFeed, savedStart } from "./orbWindow";
import { useSession } from "./session";

const CLOSED: BoxPlacement & { placed: number } = { side: "below", orbLeft: 0, placed: 0 };

/**
 * The always-on-top orb window: the orb, and on hover the latest spoken lines and the agent log. It shares the voice
 * session main owns with the full window and keeps no connection or microphone of its own; the lines are the transcript
 * events it receives, for this thread.
 */
export function OrbApp({ bridge }: { bridge: DesktopBridge }) {
  const [theme] = useAppearance();
  const [feed, dispatchFeed] = useReducer(reduceOrbFeed, emptyFeed);
  const [runs, setRuns] = useState<Runs>({});
  const [threadId, setThreadId] = useState<string>();
  const openThread = useRef<string>();
  const [placement, setPlacement] = useState(CLOSED);
  const [now, setNow] = useState(Date.now());
  const onEvent = useCallback((event: ServerEvent) => {
    dispatchFeed({ kind: "event", event });
    setRuns((previous) => reduceRuns(previous, event, Date.now()));
    if (event.type === "thread") {
      // Another thread's lines are not this one's.
      if (openThread.current !== event.thread.id) dispatchFeed({ kind: "restart" });
      openThread.current = event.thread.id;
      setThreadId(event.thread.id);
    }
  }, []);
  const picked = useMemo(() => savedStart(readSavedPick()), []);
  const session = useSession({ url: "", ...picked, onEvent });
  const { start, interrupt, state, connection } = session;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  // Every thread's runs and waiting approvals, as the workspace polls them: the socket only reports the open thread's.
  useEffect(() => {
    const poll = () =>
      fetchRuns(Date.now() - COMPLETED_LINGER_MS).then(
        (listed) => {
          dispatchFeed({ kind: "listed", approvals: listed.approvals });
          setRuns((previous) => mergeListed(previous, listed));
        },
        () => undefined,
      );
    void poll();
    const timer = window.setInterval(poll, 3000);
    return () => window.clearInterval(timer);
  }, []);

  const activity = useMemo(() => agentActivity(runs, feed.turns, threadId, now), [runs, feed.turns, threadId, now]);
  const decide = useCallback(async (approvalId: string, runId: string, approved: boolean) => {
    if (await decideApproval(approvalId, approved)) dispatchFeed({ kind: "event", event: { type: "approval.resolved", approvalId, runId, approved } });
  }, []);
  const onTalk = useCallback(() => {
    if (connection === "disconnected") void start();
    else if (state === "speaking") interrupt();
  }, [connection, state, start, interrupt]);
  const onExpand = useCallback(() => void bridge.orb.showFull(), [bridge]);
  // Only the newest answer places the box: a slower, older one must not undo it.
  const request = useRef(0);
  const onBoxHeight = useCallback(
    (height: number) => {
      const id = ++request.current;
      bridge.orb.setBox(height).then(
        (layout) => {
          if (id === request.current) setPlacement({ ...layout, placed: height });
        },
        () => undefined,
      );
    },
    [bridge],
  );

  return (
    <FloatingOrb
      mode={orbMode(connection, state, false)}
      outputLevel={session.outputLevel}
      runs={runs}
      turns={feed.turns}
      activity={activity}
      theme={theme}
      onDecide={decide}
      onTalk={onTalk}
      onExpand={onExpand}
      placement={placement}
      onBoxHeight={onBoxHeight}
    />
  );
}
