import { type Dispatch, type MutableRefObject, type SetStateAction, useEffect } from "react";
import { type ApprovalRequest, COMPLETED_LINGER_MS, type Runs, fetchRuns, mergeListed } from "@/lib/runs";

const POLL_MS = 3000;

/** Every thread's runs (the socket only reports the open thread's), polled for the Running list; the approvals they wait for land in `approvals`. */
export function useRunsPolling(setRuns: Dispatch<SetStateAction<Runs>>, approvals: MutableRefObject<ApprovalRequest[]>): void {
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
    const timer = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(timer);
  }, [setRuns, approvals]);
}
