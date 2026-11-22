import { type Dispatch, type SetStateAction, useCallback } from "react";
import { toast } from "sonner";
import type { Thread } from "@osmo/osmo-composer";
import { insertAfter, threadApi } from "@/lib/threads";

const api = threadApi();

/** The sidebar's rename, fork and delete of a thread, kept in step with the listed threads. */
export function useThreadActions(threadId: string | undefined, setThreads: Dispatch<SetStateAction<Thread[]>>, openThread: (id: string | undefined) => Promise<void>) {
  const renameThread = useCallback(
    async (id: string, title: string) => {
      try {
        const renamed = await api.rename(id, title);
        setThreads((previous) => previous.map((t) => (t.id === id ? renamed : t)));
      } catch {
        toast.error("Could not rename the thread");
      }
    },
    [setThreads],
  );
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
    [openThread, setThreads],
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
    [openThread, setThreads, threadId],
  );
  const countMessages = useCallback(async (id: string) => (await api.get(id)).messages.length, []);
  return { renameThread, forkThread, deleteThread, countMessages };
}
